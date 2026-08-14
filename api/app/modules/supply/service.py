"""Servicios de oferta y asignación (§10, §11.1, §14 del alcance).

La operación crítica es `reserve_allocation` (MATCH-02): la reserva se
serializa con SELECT … FOR UPDATE sobre la línea de oferta, recalcula la
cantidad libre bajo el lock y aplica idempotencia por Idempotency-Key
(scope "allocations"). Expiración y cancelación compensan reservas y
cobertura (§10.3).
"""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.model_base import utcnow
from app.core.outbox import publish
from app.core.state_machines import InvalidTransition, assert_transition
from app.modules.cases.models import Need, NeedCatalog, NeedStatus
from app.modules.identity.models import User
from app.modules.intake.models import IdempotencyKey
from app.modules.supply.models import (
    Allocation,
    AllocationItem,
    AllocationStatus,
    ItemCondition,
    Match,
    MatchStatus,
    OfferStatus,
    ResourceOffer,
    ResourceOfferItem,
)

logger = logging.getLogger("supply")

IDEMPOTENCY_SCOPE = "allocations"
IDEMPOTENCY_TTL_HOURS = 24

EVENT_OFFER_AVAILABLE = "offer.available"
EVENT_ALLOCATION_RESERVED = "allocation.reserved"
EVENT_ALLOCATION_EXPIRED = "allocation.expired"
EVENT_ALLOCATION_CANCELLED = "allocation.cancelled"
EVENT_NEED_COVERAGE_CHANGED = "need.coverage_changed"

ZERO = Decimal(0)


class SupplyError(Exception):
    """Base de errores de negocio del módulo supply."""


class UnknownCatalogCode(SupplyError):
    """El catalog_code no existe o no está activo en need_catalog."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"catalog_code desconocido o inactivo: {code}")


class QuantityUnavailable(SupplyError):
    """La cantidad solicitada excede la libre bajo lock (§14.3, 409)."""

    def __init__(self, available: Decimal):
        self.available = available
        super().__init__(f"cantidad no disponible; libre={available}")


class IdempotencyConflict(SupplyError):
    """Misma Idempotency-Key con request_hash distinto (§58)."""


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _free_qty(item: ResourceOfferItem) -> Decimal:
    return _dec(item.quantity) - (item.reserved_qty or ZERO) - (item.delivered_qty or ZERO)


def _active_catalog(session: Session, code: str) -> NeedCatalog | None:
    return session.execute(
        sa.select(NeedCatalog)
        .where(NeedCatalog.code == code, NeedCatalog.active.is_(True))
        .order_by(NeedCatalog.version.desc())
        .limit(1)
    ).scalar_one_or_none()


# ── Confirmación de oferta ─────────────────────────────────────────────


def confirm_offer(
    session: Session,
    offer: ResourceOffer,
    items: list[dict],
    actor: User,
) -> list[ResourceOfferItem]:
    """Crea las líneas de la oferta y la transiciona a AVAILABLE.

    Cada item: {"catalog_code": str, "quantity": num, "condition": str|None}.
    """
    created: list[ResourceOfferItem] = []
    for spec in items:
        catalog = _active_catalog(session, spec["catalog_code"])
        if catalog is None:
            raise UnknownCatalogCode(spec["catalog_code"])
        condition = spec.get("condition")
        if isinstance(condition, str):
            condition = ItemCondition(condition)
        row = ResourceOfferItem(
            id=new_id(),
            offer_id=offer.id,
            catalog_id=catalog.id,
            quantity=_dec(spec["quantity"]),
            unit_code=catalog.unit_code,
            condition=condition or ItemCondition.NOT_APPLICABLE,
        )
        session.add(row)
        created.append(row)

    if offer.status == OfferStatus.DRAFT:
        assert_transition("offer", offer.status, OfferStatus.PENDING_CONFIRMATION)
        offer.status = OfferStatus.PENDING_CONFIRMATION
    assert_transition("offer", offer.status, OfferStatus.AVAILABLE)
    offer.status = OfferStatus.AVAILABLE

    publish(
        session,
        event_type=EVENT_OFFER_AVAILABLE,
        aggregate_type="offer",
        aggregate_id=offer.id,
        payload={
            "offer_id": str(offer.id),
            "items": len(created),
            "actor_user_id": str(actor.id) if actor is not None else None,
        },
    )
    return created


# ── Estado agregado de la oferta y cobertura de la necesidad ───────────


def _recompute_offer_status(session: Session, offer: ResourceOffer) -> None:
    items = (
        session.execute(
            sa.select(ResourceOfferItem).where(ResourceOfferItem.offer_id == offer.id)
        )
        .scalars()
        .all()
    )
    if not items:
        return
    total_free = sum((_free_qty(i) for i in items), ZERO)
    any_held = any((i.reserved_qty or ZERO) + (i.delivered_qty or ZERO) > 0 for i in items)
    if total_free <= 0:
        target = OfferStatus.FULLY_ALLOCATED
    elif any_held:
        target = OfferStatus.PARTIALLY_ALLOCATED
    else:
        target = OfferStatus.AVAILABLE
    if offer.status != target:
        assert_transition("offer", offer.status, target)
        offer.status = target


_COVERAGE_STATES = {NeedStatus.OPEN, NeedStatus.PARTIALLY_COVERED, NeedStatus.COVERED}


def _apply_need_coverage(session: Session, need: Need, delta: Decimal) -> None:
    """Ajusta covered_qty (±delta) y el estado de cobertura de la necesidad.

    Referencia: confirmed_qty si existe, si no requested_qty; nunca se
    excede (CHECK covered <= confirmed) ni baja de cero.
    """
    reference = need.confirmed_qty if need.confirmed_qty is not None else need.requested_qty
    covered = (need.covered_qty or ZERO) + delta
    covered = max(covered, ZERO)
    if reference is not None:
        covered = min(covered, _dec(reference))
    need.covered_qty = covered

    if reference is not None and covered >= _dec(reference) and covered > 0:
        target = NeedStatus.COVERED
    elif covered > 0:
        target = NeedStatus.PARTIALLY_COVERED
    else:
        target = NeedStatus.OPEN

    if need.status in _COVERAGE_STATES and need.status != target:
        try:
            assert_transition("need", need.status, target)
            need.status = target
        except InvalidTransition:
            # p. ej. COVERED → PARTIALLY_COVERED pasa por OPEN (§8.2)
            assert_transition("need", need.status, NeedStatus.OPEN)
            assert_transition("need", NeedStatus.OPEN, target)
            need.status = target

    publish(
        session,
        event_type=EVENT_NEED_COVERAGE_CHANGED,
        aggregate_type="need",
        aggregate_id=need.id,
        payload={
            "need_id": str(need.id),
            "covered_qty": str(covered),
            "status": need.status.value,
        },
    )


# ── Reserva (MATCH-02) ─────────────────────────────────────────────────


def _request_hash(match_id, quantity: Decimal) -> bytes:
    return hashlib.sha256(f"{match_id}:{quantity}".encode()).digest()


def reserve_allocation(
    session: Session,
    match: Match,
    quantity,
    actor_user_id,
    idempotency_key: str | None = None,
    expires_minutes: int = 60,
) -> Allocation:
    """Reserva `quantity` del ítem del match bajo lock de fila.

    Idempotente por Idempotency-Key (scope "allocations"): misma key y
    mismo request → devuelve la Allocation ya creada; request distinto →
    IdempotencyConflict. Si la cantidad libre bajo lock no alcanza →
    QuantityUnavailable(available=libre).
    """
    qty = _dec(quantity)
    if qty <= 0:
        raise QuantityUnavailable(available=ZERO)
    now = utcnow()
    req_hash = _request_hash(match.id, qty)

    idem_row: IdempotencyKey | None = None
    if idempotency_key:
        idem_row = session.get(IdempotencyKey, (IDEMPOTENCY_SCOPE, idempotency_key))
        if idem_row is not None and idem_row.expires_at > now:
            if idem_row.request_hash != req_hash:
                raise IdempotencyConflict(
                    "Idempotency-Key ya usada con un request distinto"
                )
            existing = session.get(Allocation, uuid.UUID(idem_row.resource_id))
            if existing is not None:
                return existing

    # Lock de la línea de oferta; recálculo de libre bajo el lock.
    item = session.execute(
        sa.select(ResourceOfferItem)
        .where(ResourceOfferItem.id == match.offer_item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    free = _free_qty(item)
    if qty > free:
        raise QuantityUnavailable(available=free)

    offer = session.get(ResourceOffer, match.offer_id)
    need = session.get(Need, match.need_id)

    assert_transition("allocation", AllocationStatus.DRAFT, AllocationStatus.RESERVED)
    allocation = Allocation(
        id=new_id(),
        incident_id=offer.incident_id,
        offer_id=offer.id,
        match_id=match.id,
        status=AllocationStatus.RESERVED,
        approved_by=actor_user_id,
        expires_at=now + timedelta(minutes=expires_minutes),
    )
    session.add(allocation)
    session.add(
        AllocationItem(
            id=new_id(),
            allocation_id=allocation.id,
            offer_item_id=item.id,
            need_id=need.id,
            allocated_qty=qty,
            unit_code=item.unit_code,
        )
    )
    item.reserved_qty = (item.reserved_qty or ZERO) + qty

    if match.status in (MatchStatus.PROPOSED, MatchStatus.REVIEW_REQUIRED):
        assert_transition("match", match.status, MatchStatus.APPROVED)
        match.status = MatchStatus.APPROVED

    _recompute_offer_status(session, offer)
    _apply_need_coverage(session, need, qty)

    publish(
        session,
        event_type=EVENT_ALLOCATION_RESERVED,
        aggregate_type="allocation",
        aggregate_id=allocation.id,
        payload={
            "allocation_id": str(allocation.id),
            "match_id": str(match.id),
            "offer_item_id": str(item.id),
            "quantity": str(qty),
        },
    )

    if idempotency_key:
        fields = {
            "request_hash": req_hash,
            "response_code": 201,
            "resource_type": "allocation",
            "resource_id": str(allocation.id),
            "expires_at": now + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
        }
        if idem_row is not None:  # fila expirada: se reutiliza
            for k, v in fields.items():
                setattr(idem_row, k, v)
        else:
            session.add(
                IdempotencyKey(scope=IDEMPOTENCY_SCOPE, key=idempotency_key, **fields)
            )
    return allocation


# ── Compensación: expiración y cancelación (§10.3) ─────────────────────


def _release_allocation(session: Session, allocation: Allocation) -> None:
    """Libera reserved_qty de los ítems y covered_qty de las necesidades."""
    rows = (
        session.execute(
            sa.select(AllocationItem).where(AllocationItem.allocation_id == allocation.id)
        )
        .scalars()
        .all()
    )
    offers_touched: set[uuid.UUID] = set()
    for row in rows:
        item = session.execute(
            sa.select(ResourceOfferItem)
            .where(ResourceOfferItem.id == row.offer_item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        qty = _dec(row.allocated_qty)
        item.reserved_qty = max((item.reserved_qty or ZERO) - qty, ZERO)
        offers_touched.add(item.offer_id)
        need = session.get(Need, row.need_id)
        if need is not None:
            _apply_need_coverage(session, need, -qty)
    for offer_id in offers_touched:
        offer = session.get(ResourceOffer, offer_id)
        _recompute_offer_status(session, offer)


def expire_allocations(session: Session, now: datetime | None = None) -> int:
    """Expira reservas vencidas y compensa; devuelve cuántas expiró."""
    now = now or utcnow()
    rows = (
        session.execute(
            sa.select(Allocation)
            .where(
                Allocation.status == AllocationStatus.RESERVED,
                Allocation.expires_at < now,
            )
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for allocation in rows:
        assert_transition("allocation", allocation.status, AllocationStatus.EXPIRED)
        allocation.status = AllocationStatus.EXPIRED
        _release_allocation(session, allocation)
        publish(
            session,
            event_type=EVENT_ALLOCATION_EXPIRED,
            aggregate_type="allocation",
            aggregate_id=allocation.id,
            payload={"allocation_id": str(allocation.id)},
        )
    return len(rows)


def cancel_allocation(
    session: Session,
    allocation: Allocation,
    actor_user_id,
    reason: str,
) -> Allocation:
    """Cancela una asignación con compensación idéntica a la expiración."""
    previous = allocation.status
    assert_transition("allocation", previous, AllocationStatus.CANCELLED)
    allocation.status = AllocationStatus.CANCELLED
    if previous != AllocationStatus.DRAFT:
        _release_allocation(session, allocation)
    publish(
        session,
        event_type=EVENT_ALLOCATION_CANCELLED,
        aggregate_type="allocation",
        aggregate_id=allocation.id,
        payload={
            "allocation_id": str(allocation.id),
            "reason": reason,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
        },
    )
    return allocation
