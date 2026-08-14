"""Webhook de Meta WhatsApp (§4.1–4.2 del alcance).

Secuencia de ingreso: validar tamaño y firma ANTES de deserializar,
persistir idempotentemente el envelope cifrado, publicar evento al
outbox y responder 2xx rápido. Todo el procesamiento pesado ocurre en
el worker.
"""

import hashlib
import json
import logging

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.ids import new_id
from app.core.logging import log_ctx
from app.core.model_base import utcnow
from app.core.outbox import publish
from app.core.security import encrypt_json
from app.integrations.meta_whatsapp.signature import verify_signature
from app.modules.identity.models import ChannelType
from app.modules.intake.models import (
    Channel,
    ChannelStatus,
    ProviderType,
    WebhookProcessingStatus,
    WebhookReceipt,
)

logger = logging.getLogger("webhook")

router = APIRouter()

MAX_BODY_BYTES = 512 * 1024  # los webhooks de Meta son pequeños; media va aparte

EVENT_WEBHOOK_RECEIVED = "whatsapp.webhook.received"


@router.get("/webhooks/meta/whatsapp", include_in_schema=False)
def verify_challenge(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    settings = get_settings()
    if (
        hub_mode == "subscribe"
        and settings.meta_webhook_verify_token
        and hub_verify_token == settings.meta_webhook_verify_token
    ):
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


def _get_or_create_channel(session, phone_number_id: str | None) -> Channel:
    """Canal por phone_number_id; se crea en TEST si no existe (sandbox)."""
    q = sa.select(Channel).where(Channel.provider == ProviderType.META_CLOUD_API)
    if phone_number_id:
        q = q.where(Channel.phone_number_id == phone_number_id)
    channel = session.execute(q.limit(1)).scalar_one_or_none()
    if channel is None:
        channel = Channel(
            type=ChannelType.WHATSAPP,
            provider=ProviderType.META_CLOUD_API,
            phone_number_id=phone_number_id,
            display_name="WhatsApp Colombia Unida",
            status=ChannelStatus.TEST,
            config={},
        )
        session.add(channel)
        session.flush()
    return channel


@router.post("/webhooks/meta/whatsapp", include_in_schema=False)
async def receive_webhook(request: Request):
    settings = get_settings()

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return Response(status_code=413)

    signature = request.headers.get("X-Hub-Signature-256")
    if settings.meta_app_secret and not verify_signature(
        settings.meta_app_secret, body, signature
    ):
        log_ctx(logger, logging.WARNING, "webhook signature invalid")
        return Response(status_code=403)

    try:
        payload = json.loads(body)
    except ValueError:
        return Response(status_code=400)

    # Meta no envía un event id global: derivamos uno estable del cuerpo,
    # así los reintentos de entrega idénticos deduplican aquí (WA-01).
    provider_event_id = hashlib.sha256(body).hexdigest()

    phone_number_id = None
    try:
        phone_number_id = payload["entry"][0]["changes"][0]["value"]["metadata"][
            "phone_number_id"
        ]
    except (KeyError, IndexError, TypeError):
        pass

    session = get_session_factory()()
    try:
        channel = _get_or_create_channel(session, phone_number_id)
        receipt_id = new_id()
        stmt = (
            pg_insert(WebhookReceipt)
            .values(
                id=receipt_id,
                channel_id=channel.id,
                provider_event_id=provider_event_id,
                event_type="whatsapp",
                signature_valid=bool(signature),
                payload_enc=encrypt_json(payload),
                received_at=utcnow(),
                processing_status=WebhookProcessingStatus.RECEIVED,
                attempt_count=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["channel_id", "provider_event_id"])
            .returning(WebhookReceipt.id)
        )
        inserted = session.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            publish(
                session,
                event_type=EVENT_WEBHOOK_RECEIVED,
                aggregate_type="webhook_receipt",
                aggregate_id=inserted,
            )
        session.commit()
        if inserted is None:
            log_ctx(logger, logging.INFO, "webhook duplicate ignored")
    except Exception:
        session.rollback()
        logger.exception("webhook persistence failed")
        # 500 → Meta reintenta; el INSERT idempotente absorbe el duplicado
        return Response(status_code=500)
    finally:
        session.close()

    return {"status": "received"}
