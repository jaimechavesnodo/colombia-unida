"""Procesamiento asíncrono de webhooks de WhatsApp (conversation-worker).

Handler idempotente del evento whatsapp.webhook.received: crea/actualiza
persona, conversación y mensajes; corre el motor conversacional; envía
las respuestas por Graph API y las persiste como mensajes salientes.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_id
from app.core.logging import log_ctx
from app.core.model_base import utcnow
from app.core.outbox import OutboxEvent, publish, register_handler
from app.core.security import decrypt_json, encrypt_text, phone_hmac
from app.integrations.meta_whatsapp.client import GraphClient
from app.integrations.meta_whatsapp.parser import InboundMessage, parse_webhook
from app.modules.identity.models import (
    IdentifierType,
    Person,
    PersonIdentifier,
)
from app.modules.intake import engine as bot_engine
from app.modules.intake.models import (
    Conversation,
    ConversationStatus,
    MalwareStatus,
    MediaAsset,
    Message,
    MessageDeliveryStatus,
    MessageDirection,
    MessageProcessingStatus,
    MessageType,
    StorageClassification,
    WebhookProcessingStatus,
    WebhookReceipt,
)

logger = logging.getLogger("intake")

MSG_TYPE_MAP = {
    "text": MessageType.TEXT,
    "audio": MessageType.AUDIO,
    "image": MessageType.IMAGE,
    "location": MessageType.LOCATION,
    "video": MessageType.VIDEO,
    "document": MessageType.DOCUMENT,
    "interactive": MessageType.INTERACTIVE,
    "button": MessageType.INTERACTIVE,
}

STATUS_MAP = {
    "sent": MessageDeliveryStatus.SENT,
    "delivered": MessageDeliveryStatus.DELIVERED,
    "read": MessageDeliveryStatus.READ,
    "failed": MessageDeliveryStatus.FAILED,
}


def _default_incident_id(session: Session):
    from app.modules.identity.models import Incident, IncidentStatus

    row = session.execute(
        sa.select(Incident.id)
        .where(Incident.status == IncidentStatus.ACTIVE)
        .order_by(Incident.created_at)
        .limit(1)
    ).first()
    return row[0] if row else None


def _find_or_create_person(session: Session, wa_id: str, profile_name: str | None):
    """Busca por HMAC del teléfono; crea PERSON parcial si no existe (§5.1-2).

    Teléfono no equivale a afectado: la persona nace PARTIAL y sus roles
    se definen por caso.
    """
    hm = phone_hmac(wa_id)
    ident = session.execute(
        sa.select(PersonIdentifier).where(
            PersonIdentifier.type == IdentifierType.PHONE,
            PersonIdentifier.value_hmac == hm,
        )
    ).scalar_one_or_none()
    if ident:
        return ident.person_id
    person = Person(
        display_name_enc=encrypt_text(profile_name) if profile_name else None,
    )
    session.add(person)
    session.flush()
    session.add(
        PersonIdentifier(
            person_id=person.id,
            type=IdentifierType.PHONE,
            value_enc=encrypt_text(wa_id),
            value_hmac=hm,
            last4=wa_id[-4:],
            is_primary=True,
        )
    )
    return person.id


def _find_or_create_conversation(session: Session, channel_id, wa_id: str) -> Conversation:
    hm = phone_hmac(wa_id)
    conv = session.execute(
        sa.select(Conversation)
        .where(
            Conversation.channel_id == channel_id,
            Conversation.external_thread_key_hmac == hm,
            Conversation.status != ConversationStatus.CLOSED,
        )
        .order_by(Conversation.last_message_at.desc().nulls_last())
        .limit(1)
    ).scalar_one_or_none()
    if conv:
        return conv
    conv = Conversation(
        incident_id=_default_incident_id(session),
        channel_id=channel_id,
        external_thread_key_hmac=hm,
        status=ConversationStatus.OPEN,
    )
    session.add(conv)
    session.flush()
    return conv


def _insert_inbound_message(
    session: Session, conversation: Conversation, msg: InboundMessage
) -> Message | None:
    """Inserta idempotente por provider_message_id; None si ya existía."""
    mid = new_id()
    stmt = (
        pg_insert(Message)
        .values(
            id=mid,
            conversation_id=conversation.id,
            provider_message_id=msg.provider_message_id,
            direction=MessageDirection.INBOUND,
            type=MSG_TYPE_MAP.get(msg.message_type, MessageType.TEXT),
            text_enc=encrypt_text(msg.text) if msg.text else None,
            normalized_text_redacted=(msg.text or "")[:2000] or None,
            provider_timestamp=msg.timestamp,
            received_at=utcnow(),
            delivery_status=MessageDeliveryStatus.DELIVERED,
            processing_status=MessageProcessingStatus.RECEIVED,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["provider_message_id"])
        .returning(Message.id)
    )
    inserted = session.execute(stmt).scalar_one_or_none()
    if inserted is None:
        return None
    row = session.get(Message, inserted)
    if msg.media_id:
        asset = MediaAsset(
            message_id=row.id,
            bucket_class=StorageClassification.QUARANTINE,
            object_uri=f"meta-media://{msg.media_id}",  # media_service descarga y reemplaza
            mime_type=msg.media_mime_type,
            malware_status=MalwareStatus.PENDING,
            exif_removed=False,
        )
        session.add(asset)
        session.flush()
        publish(
            session,
            event_type="media.received",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
        )
    return row


def _record_outbound(
    session: Session, conversation: Conversation, text: str,
    provider_message_id: str | None,
) -> None:
    session.add(
        Message(
            conversation_id=conversation.id,
            provider_message_id=provider_message_id,
            direction=MessageDirection.OUTBOUND,
            type=MessageType.TEXT,
            text_enc=encrypt_text(text),
            provider_timestamp=utcnow(),
            received_at=utcnow(),
            delivery_status=(
                MessageDeliveryStatus.SENT
                if provider_message_id
                else MessageDeliveryStatus.QUEUED
            ),
            processing_status=MessageProcessingStatus.PROCESSED,
        )
    )


def process_webhook_receipt(session: Session, event: OutboxEvent) -> None:
    """Handler idempotente del evento whatsapp.webhook.received."""
    receipt = session.get(WebhookReceipt, event.aggregate_id)
    if receipt is None:
        return
    if receipt.processing_status == WebhookProcessingStatus.PROCESSED:
        return  # reintento del outbox: ya procesado

    payload = decrypt_json(receipt.payload_enc)
    parsed = parse_webhook(payload)

    settings = get_settings()
    client = None
    if settings.meta_access_token and settings.meta_phone_number_id:
        client = GraphClient()

    for msg in parsed.messages:
        person_id = _find_or_create_person(session, msg.wa_id, msg.profile_name)
        conversation = _find_or_create_conversation(session, receipt.channel_id, msg.wa_id)
        row = _insert_inbound_message(session, conversation, msg)
        if row is None:
            continue  # duplicado (WA-01): sin efecto de dominio adicional
        conversation.last_message_at = utcnow()

        result = bot_engine.handle_message(session, conversation, person_id, row)
        for reply in result.replies:
            provider_id = None
            if client is not None:
                try:
                    provider_id = client.send_text(msg.wa_id, reply.text)
                except Exception:
                    log_ctx(
                        logger, logging.ERROR, "send failed",
                        prompt_code=reply.prompt_code,
                    )
            _record_outbound(session, conversation, reply.text, provider_id)

    for status in parsed.statuses:
        target = session.execute(
            sa.select(Message).where(
                Message.provider_message_id == status.provider_message_id
            )
        ).scalar_one_or_none()
        new_status = STATUS_MAP.get(status.status)
        if target is not None and new_status is not None:
            # No degradar READ→DELIVERED en entregas fuera de orden (§4.3)
            order = ["QUEUED", "SENT", "DELIVERED", "READ", "FAILED"]
            if (
                new_status == MessageDeliveryStatus.FAILED
                or order.index(new_status.value) > order.index(target.delivery_status.value)
            ):
                target.delivery_status = new_status

    if parsed.unknown_events:
        log_ctx(
            logger, logging.WARNING, "whatsapp.unknown events",
            count=len(parsed.unknown_events),
        )

    receipt.processing_status = WebhookProcessingStatus.PROCESSED


def register() -> None:
    register_handler("whatsapp.webhook.received", process_webhook_receipt)
