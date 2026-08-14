"""Pruebas E2E del canal WhatsApp (criterios §19.2: WA-01, WA-02, WA-03).

Simulan webhooks de Meta firmados contra la app real, con el worker
procesando el outbox entre mensajes. Requieren PostgreSQL (se saltan
sin servidor).
"""

import hashlib
import hmac
import json
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

TEST_APP_SECRET = "test-app-secret"
TEST_VERIFY_TOKEN = "test-verify-token"


@pytest.fixture(scope="module", autouse=True)
def _env(migrated_engine):
    os.environ["META_APP_SECRET"] = TEST_APP_SECRET
    os.environ["META_WEBHOOK_VERIFY_TOKEN"] = TEST_VERIFY_TOKEN
    os.environ["META_ACCESS_TOKEN"] = ""  # sin cliente: respuestas quedan QUEUED
    from app.core.config import get_settings

    get_settings.cache_clear()
    # Seeds necesarios (incidente activo + municipios)
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    session = factory()
    from app.seeds.__main__ import seed_divipola, seed_incident, seed_roles

    seed_roles(session)
    seed_incident(session)
    seed_divipola(session)
    session.commit()
    session.close()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(migrated_engine):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture
def factory(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(TEST_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload_text(wa_id: str, text: str, msg_id: str | None = None) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "573000000000",
                        "phone_number_id": "PHONE_ID_TEST",
                    },
                    "contacts": [{"profile": {"name": "Prueba"}, "wa_id": wa_id}],
                    "messages": [{
                        "from": wa_id,
                        "id": msg_id or f"wamid.{uuid.uuid4().hex}",
                        "timestamp": "1786665600",
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }


def _post(client, payload: dict):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/meta/whatsapp",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )


def _drain(factory):
    from app.core import outbox
    from app.modules.intake import service

    outbox.HANDLERS.clear()
    service.register()
    while outbox.process_batch(factory):
        pass


def _send_and_process(client, factory, wa_id: str, text: str, msg_id: str | None = None):
    resp = _post(client, _payload_text(wa_id, text, msg_id))
    assert resp.status_code == 200
    _drain(factory)


def _last_bot_reply(factory, wa_id: str) -> str:
    from app.core.security import decrypt_text, phone_hmac
    from app.modules.intake.models import Conversation, Message, MessageDirection

    s = factory()
    try:
        conv = s.execute(
            sa.select(Conversation).where(
                Conversation.external_thread_key_hmac == phone_hmac(wa_id)
            )
        ).scalar_one()
        msg = s.execute(
            sa.select(Message)
            .where(
                Message.conversation_id == conv.id,
                Message.direction == MessageDirection.OUTBOUND,
            )
            .order_by(Message.received_at.desc(), Message.id.desc())
            .limit(1)
        ).scalar_one()
        return decrypt_text(msg.text_enc)
    finally:
        s.close()


def test_challenge_verification(client):
    resp = client.get(
        "/webhooks/meta/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": TEST_VERIFY_TOKEN,
            "hub.challenge": "12345",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "12345"

    resp = client.get(
        "/webhooks/meta/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "12345",
        },
    )
    assert resp.status_code == 403


def test_invalid_signature_rejected(client, factory):
    body = json.dumps(_payload_text("573001110001", "hola")).encode()
    resp = client.post(
        "/webhooks/meta/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


def test_wa01_same_webhook_five_times_single_effect(client, factory):
    """WA-01: el mismo webhook entregado 5 veces crea un solo message."""
    wa_id = "573001110002"
    payload = _payload_text(wa_id, "Hola", msg_id="wamid.WA01FIXED")
    for _ in range(5):
        resp = _post(client, payload)
        assert resp.status_code == 200
    _drain(factory)

    from app.modules.intake.models import Message, WebhookReceipt

    s = factory()
    try:
        receipts = s.execute(
            sa.select(sa.func.count()).select_from(WebhookReceipt)
        ).scalar()
        msgs = s.execute(
            sa.select(sa.func.count())
            .select_from(Message)
            .where(Message.provider_message_id == "wamid.WA01FIXED")
        ).scalar()
        assert msgs == 1
        assert receipts >= 1  # los 4 duplicados no crearon receipts nuevos
    finally:
        s.close()


def test_full_need_help_flow_submitted(client, factory):
    """Flujo §5.1 completo: hola → intent → consent → narrativa →
    municipio → hogar → enviar. Cada mensaje es un webhook separado
    (WA-02: el draft sobrevive entre entregas)."""
    wa_id = "573001110003"
    _send_and_process(client, factory, wa_id, "Hola")
    assert "Necesito ayuda" in _last_bot_reply(factory, wa_id)

    _send_and_process(client, factory, wa_id, "1")
    assert "Acepto" in _last_bot_reply(factory, wa_id)

    _send_and_process(client, factory, wa_id, "1")
    reply = _last_bot_reply(factory, wa_id)
    assert "CU-" in reply

    _send_and_process(client, factory, wa_id, "Se cayó parte de la casa y necesitamos colchones")
    assert "municipio" in _last_bot_reply(factory, wa_id).lower()

    _send_and_process(client, factory, wa_id, "Manizales")
    assert "personas" in _last_bot_reply(factory, wa_id).lower()

    _send_and_process(client, factory, wa_id, "5")
    assert "Enviar" in _last_bot_reply(factory, wa_id)

    _send_and_process(client, factory, wa_id, "1")
    assert "enviada" in _last_bot_reply(factory, wa_id)

    from app.modules.cases.models import Case, CaseStatus, Report, ReportStatus
    from app.modules.identity.models import Consent, ConsentStatus

    s = factory()
    try:
        report = s.execute(
            sa.select(Report).order_by(Report.created_at.desc()).limit(1)
        ).scalar_one()
        assert report.status == ReportStatus.SUBMITTED
        assert "colchones" in (report.narrative or "")
        case = s.get(Case, s.execute(
            sa.select(Case.id).where(Case.status == CaseStatus.PENDING_VERIFICATION)
            .order_by(Case.created_at.desc()).limit(1)
        ).scalar_one())
        assert case.case_code.startswith("CU-")
        assert case.household_id is not None
        consents = s.execute(
            sa.select(sa.func.count()).select_from(Consent).where(
                Consent.status == ConsentStatus.GRANTED
            )
        ).scalar()
        assert consents >= 3
    finally:
        s.close()


def test_wa03_complete_later_leaves_incomplete(client, factory):
    """WA-03: 'completar después' deja SUBMITTED_INCOMPLETE con faltantes."""
    wa_id = "573001110004"
    _send_and_process(client, factory, wa_id, "Hola")
    _send_and_process(client, factory, wa_id, "1")
    _send_and_process(client, factory, wa_id, "1")
    _send_and_process(client, factory, wa_id, "Necesitamos agua y mercado")
    _send_and_process(client, factory, wa_id, "3")  # completar después

    from app.core.security import phone_hmac
    from app.modules.cases.models import Case, CaseStatus, Report, ReportStatus
    from app.modules.identity.models import PersonIdentifier
    from app.modules.intake.models import Conversation

    s = factory()
    try:
        person_id = s.execute(
            sa.select(PersonIdentifier.person_id).where(
                PersonIdentifier.value_hmac == phone_hmac(wa_id)
            )
        ).scalar_one()
        report = s.execute(
            sa.select(Report).where(Report.reporter_person_id == person_id)
        ).scalar_one()
        assert report.status == ReportStatus.SUBMITTED_INCOMPLETE
        conv = s.execute(
            sa.select(Conversation).where(
                Conversation.external_thread_key_hmac == phone_hmac(wa_id)
            )
        ).scalar_one()
        case = s.get(Case, conv.active_case_id)
        assert case.status == CaseStatus.INCOMPLETE
    finally:
        s.close()

    # Puede retomar después: consultar el caso por código
    s = factory()
    case_code = case.case_code
    s.close()
    _send_and_process(client, factory, wa_id, case_code)
    assert "faltan datos" in _last_bot_reply(factory, wa_id)


def test_agent_handoff_silences_bot(client, factory):
    wa_id = "573001110005"
    _send_and_process(client, factory, wa_id, "Hola")
    _send_and_process(client, factory, wa_id, "AGENTE")
    assert "persona" in _last_bot_reply(factory, wa_id).lower()

    from app.core.security import phone_hmac
    from app.modules.intake.models import (
        AgentMode,
        AgentQueueItem,
        Conversation,
        Message,
        MessageDirection,
    )

    s = factory()
    try:
        conv = s.execute(
            sa.select(Conversation).where(
                Conversation.external_thread_key_hmac == phone_hmac(wa_id)
            )
        ).scalar_one()
        assert conv.agent_mode == AgentMode.HUMAN
        queue = s.execute(
            sa.select(sa.func.count()).select_from(AgentQueueItem).where(
                AgentQueueItem.conversation_id == conv.id
            )
        ).scalar()
        assert queue == 1
        before = s.execute(
            sa.select(sa.func.count()).select_from(Message).where(
                Message.conversation_id == conv.id,
                Message.direction == MessageDirection.OUTBOUND,
            )
        ).scalar()
    finally:
        s.close()

    # En modo HUMAN el bot calla (§6.2)
    _send_and_process(client, factory, wa_id, "sigo esperando")
    s = factory()
    try:
        after = s.execute(
            sa.select(sa.func.count()).select_from(Message).where(
                Message.conversation_id == conv.id,
                Message.direction == MessageDirection.OUTBOUND,
            )
        ).scalar()
        assert after == before
    finally:
        s.close()


def test_offer_help_flow_creates_offer(client, factory):
    wa_id = "573001110006"
    _send_and_process(client, factory, wa_id, "Hola")
    _send_and_process(client, factory, wa_id, "3")
    assert "tipo de ayuda" in _last_bot_reply(factory, wa_id).lower()
    _send_and_process(client, factory, wa_id, "2")
    _send_and_process(client, factory, wa_id, "10 colchones nuevos en Bogotá")
    assert "OF-" in _last_bot_reply(factory, wa_id)

    from app.modules.supply.models import OfferStatus, ResourceOffer, ResourceType

    s = factory()
    try:
        offer = s.execute(
            sa.select(ResourceOffer).order_by(ResourceOffer.created_at.desc()).limit(1)
        ).scalar_one()
        assert offer.type == ResourceType.IN_KIND
        assert offer.status == OfferStatus.PENDING_CONFIRMATION
    finally:
        s.close()
