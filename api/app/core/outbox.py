"""Transactional outbox (§1.4-5, §3.3 del alcance).

- `publish()` inserta el evento EN LA MISMA transacción que el cambio de
  dominio; el commit del llamador confirma ambos.
- `process_batch()` lo consume el worker con SELECT … FOR UPDATE SKIP
  LOCKED, reintentos con backoff y DLQ (publish_status=DEAD_LETTER).
- Los handlers deben ser idempotentes: pueden ejecutarse más de una vez.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.logging import log_ctx
from app.core.model_base import utcnow
from app.modules.intake.models import OutboxEvent, OutboxStatus

logger = logging.getLogger("outbox")

MAX_RETRIES = 8
BACKOFF_BASE_SECONDS = 30

# event_type -> handler(session, event). Registrados por los módulos.
HANDLERS: dict[str, list[Callable[[Session, OutboxEvent], None]]] = {}


def register_handler(event_type: str, handler: Callable[[Session, OutboxEvent], None]) -> None:
    HANDLERS.setdefault(event_type, []).append(handler)


def publish(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id,
    payload: dict[str, Any] | None = None,
    schema_version: int = 1,
    correlation_id=None,
) -> OutboxEvent:
    """Encola un evento de dominio dentro de la transacción actual.

    El payload debe ser mínimo y sin datos P3 (§7.3-57).
    """
    event = OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        schema_version=schema_version,
        payload_redacted=payload or {},
        occurred_at=utcnow(),
        publish_status=OutboxStatus.PENDING,
        retry_count=0,
        correlation_id=correlation_id or uuid.uuid4(),
    )
    session.add(event)
    return event


def _claim_batch(session: Session, batch_size: int) -> list[OutboxEvent]:
    now = utcnow()
    rows = (
        session.execute(
            sa.select(OutboxEvent)
            .where(
                OutboxEvent.publish_status.in_([OutboxStatus.PENDING, OutboxStatus.RETRY]),
                sa.or_(
                    OutboxEvent.next_attempt_at.is_(None),
                    OutboxEvent.next_attempt_at <= now,
                ),
            )
            .order_by(OutboxEvent.occurred_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.locked_at = now
    return list(rows)


def process_batch(session_factory, batch_size: int = 50) -> int:
    """Procesa un lote; devuelve cuántos eventos se manejaron."""
    session: Session = session_factory()
    handled = 0
    try:
        events = _claim_batch(session, batch_size)
        for event in events:
            try:
                for handler in HANDLERS.get(event.event_type, []):
                    handler(session, event)
                event.publish_status = OutboxStatus.PUBLISHED
                event.published_at = utcnow()
                event.last_error_redacted = None
            except Exception as exc:  # noqa: BLE001 — el worker no debe morir por un evento
                event.retry_count = (event.retry_count or 0) + 1
                if event.retry_count >= MAX_RETRIES:
                    event.publish_status = OutboxStatus.DEAD_LETTER
                    log_ctx(
                        logger, logging.ERROR, "outbox event dead-lettered",
                        event_type=event.event_type, event_id=str(event.id),
                        retries=event.retry_count,
                    )
                else:
                    event.publish_status = OutboxStatus.RETRY
                    delay = BACKOFF_BASE_SECONDS * (2 ** (event.retry_count - 1))
                    event.next_attempt_at = utcnow() + timedelta(seconds=min(delay, 3600))
                event.last_error_redacted = type(exc).__name__
            finally:
                event.locked_at = None
                handled += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return handled
