"""Confirmación humana de candidatos IA (§9.1): AI-01 y promoción."""

import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="module", autouse=True)
def _env(migrated_engine):
    os.environ["META_ACCESS_TOKEN"] = ""
    from app.core.config import get_settings

    get_settings.cache_clear()
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    s = factory()
    from app.seeds.__main__ import seed_divipola, seed_incident, seed_need_catalog, seed_roles

    seed_roles(s)
    seed_incident(s)
    seed_divipola(s)
    seed_need_catalog(s)
    s.commit()
    s.close()
    yield
    get_settings.cache_clear()


@pytest.fixture
def factory(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


def _setup_case_with_candidates(s):
    """Crea persona, conversación, caso, mensaje, run y candidatos PROPOSED."""
    from app.core.ids import new_short_code
    from app.core.model_base import utcnow
    from app.core.security import encrypt_json, phone_hmac
    from app.modules.cases.models import Case, CaseStatus
    from app.modules.identity.models import ChannelType, Incident, Person
    from app.modules.intake.models import (
        AiExtractionRun,
        AiRunStatus,
        AiTaskType,
        CandidateStatus,
        Channel,
        ChannelStatus,
        Conversation,
        ConversationStatus,
        ExtractionCandidate,
        Message,
        MessageDeliveryStatus,
        MessageDirection,
        MessageProcessingStatus,
        MessageType,
        ProviderType,
    )

    incident_id = s.execute(sa.select(Incident.id).limit(1)).scalar_one()
    channel = s.execute(sa.select(Channel).limit(1)).scalar_one_or_none()
    if channel is None:
        channel = Channel(
            type=ChannelType.WHATSAPP,
            provider=ProviderType.META_CLOUD_API,
            status=ChannelStatus.TEST,
            config={},
        )
        s.add(channel)
        s.flush()
    person = Person()
    s.add(person)
    s.flush()
    wa = f"5730099{uuid.uuid4().hex[:5]}"
    conv = Conversation(
        incident_id=incident_id,
        channel_id=channel.id,
        external_thread_key_hmac=phone_hmac(wa),
        status=ConversationStatus.OPEN,
    )
    s.add(conv)
    s.flush()
    case = Case(
        incident_id=incident_id,
        case_code=new_short_code("CU"),
        case_type="HOUSEHOLD",
        status=CaseStatus.INCOMPLETE,
    )
    s.add(case)
    s.flush()
    conv.active_case_id = case.id
    msg = Message(
        conversation_id=conv.id,
        provider_message_id=f"wamid.{uuid.uuid4().hex}",
        direction=MessageDirection.INBOUND,
        type=MessageType.TEXT,
        provider_timestamp=utcnow(),
        received_at=utcnow(),
        delivery_status=MessageDeliveryStatus.DELIVERED,
        processing_status=MessageProcessingStatus.RECEIVED,
    )
    s.add(msg)
    s.flush()
    run = AiExtractionRun(
        message_id=msg.id,
        task_type=AiTaskType.EXTRACT_CASE,
        provider="anthropic",
        model_version="test-model",
        prompt_version="v1",
        input_hash=os.urandom(32),
        status=AiRunStatus.SUCCEEDED,
        confidence=0.9,
    )
    s.add(run)
    s.flush()
    cands = [
        ExtractionCandidate(
            ai_run_id=run.id,
            target_entity_type="need",
            field_path="needs[0]",
            proposed_value_enc=encrypt_json({"catalog_code": "SHELTER.MATTRESS"}),
            normalized_value={
                "catalog_code": "SHELTER.MATTRESS",
                "free_text": "colchones",
                "quantity": 3,
                "horizon": "EMERGENCY",
            },
            confidence=0.92,
            status=CandidateStatus.PROPOSED,
        ),
        ExtractionCandidate(
            ai_run_id=run.id,
            target_entity_type="case",
            field_path="location.municipality_text",
            proposed_value_enc=encrypt_json({"value": "Manizales"}),
            normalized_value={"value": "Manizales"},
            confidence=0.85,
            status=CandidateStatus.PROPOSED,
        ),
    ]
    s.add_all(cands)
    s.flush()
    return person, conv, case, msg, run, cands


def test_confirmation_prompt_sets_state_and_records_outbound(factory):
    from app.core import outbox
    from app.core.security import decrypt_json
    from app.modules.intake import confirmation_service

    s = factory()
    person, conv, case, msg, run, cands = _setup_case_with_candidates(s)
    ev = outbox.publish(
        s, event_type=confirmation_service.EVENT_EXTRACTION_COMPLETED,
        aggregate_type="ai_run", aggregate_id=run.id,
    )
    s.flush()
    confirmation_service.send_confirmation_prompt(s, ev)
    s.commit()

    state = decrypt_json(conv.context_summary_enc)
    assert state["flow"] == "AWAIT_AI_CONFIRM"
    assert state["ai_run_id"] == str(run.id)

    from app.modules.intake.models import Message, MessageDirection

    outb = s.execute(
        sa.select(Message).where(
            Message.conversation_id == conv.id,
            Message.direction == MessageDirection.OUTBOUND,
        )
    ).scalars().all()
    assert len(outb) == 1
    s.close()


def test_accept_promotes_needs_and_confirms(factory):
    """AI-01: el dato IA solo se promueve con confirmación; queda provenance."""
    from app.modules.intake import confirmation_service
    from app.modules.intake.models import CandidateStatus, HumanConfirmation

    s = factory()
    person, conv, case, msg, run, cands = _setup_case_with_candidates(s)
    # estado como lo dejaría send_confirmation_prompt
    from app.modules.intake import engine as bot_engine

    state = {"flow": "AWAIT_AI_CONFIRM", "ai_run_id": str(run.id), "case_code": case.case_code}
    bot_engine._save_state(conv, state)

    from app.modules.cases.models import Need, NeedStatus

    # Antes de confirmar: sin needs (AI-01, no se promueve solo por confianza)
    assert s.execute(
        sa.select(sa.func.count()).select_from(Need).where(Need.case_id == case.id)
    ).scalar() == 0

    reply = confirmation_service.handle_confirmation_reply(s, conv, person.id, msg, "1")
    s.commit()
    assert "Registramos" in reply

    needs = s.execute(sa.select(Need).where(Need.case_id == case.id)).scalars().all()
    assert len(needs) == 1
    assert needs[0].status == NeedStatus.REPORTED
    assert float(needs[0].requested_qty) == 3
    assert needs[0].catalog_id is not None

    confs = s.execute(sa.select(sa.func.count()).select_from(HumanConfirmation)).scalar()
    assert confs >= 2
    for c in cands:
        s.refresh(c)
        assert c.status == CandidateStatus.CONFIRMED
    s.close()


def test_defer_keeps_candidates_unpromoted(factory):
    from app.modules.cases.models import Need
    from app.modules.intake import confirmation_service
    from app.modules.intake import engine as bot_engine
    from app.modules.intake.models import CandidateStatus

    s = factory()
    person, conv, case, msg, run, cands = _setup_case_with_candidates(s)
    bot_engine._save_state(conv, {"flow": "AWAIT_AI_CONFIRM", "ai_run_id": str(run.id)})

    reply = confirmation_service.handle_confirmation_reply(s, conv, person.id, msg, "3")
    s.commit()
    assert "Guardado" in reply
    assert s.execute(
        sa.select(sa.func.count()).select_from(Need).where(Need.case_id == case.id)
    ).scalar() == 0
    for c in cands:
        s.refresh(c)
        assert c.status == CandidateStatus.DEFERRED
    s.close()
