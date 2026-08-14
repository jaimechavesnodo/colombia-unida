"""Pruebas del pipeline de IA (§9.1): STT, extracción LLM y orquestación.

Sin red real: httpx y el SDK anthropic se sustituyen por dobles. Las
pruebas de orquestación usan la BD migrada (se saltan sin servidor).
"""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa

# ── STT ────────────────────────────────────────────────────────────────


def _stt_settings(**overrides):
    base = {"stt_provider": "disabled", "openai_api_key": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_stt_openai_verbose_json(monkeypatch):
    from app.integrations import stt

    monkeypatch.setattr(
        stt, "get_settings",
        lambda: _stt_settings(stt_provider="openai", openai_api_key="sk-test"),
    )
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured.update(url=url, headers=headers, data=data, files=files, timeout=timeout)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "text": "hola necesito agua",
                "language": "spanish",
                "segments": [{"avg_logprob": -0.1}, {"avg_logprob": -0.3}],
            },
        )

    monkeypatch.setattr(stt.httpx, "post", fake_post)
    result = stt.transcribe(b"OggS-fake-bytes", "audio/ogg; codecs=opus")

    assert result.text == "hola necesito agua"
    assert result.language == "spanish"
    assert result.provider == "openai"
    assert result.model == "whisper-1"
    assert result.confidence is not None and 0.0 < result.confidence <= 1.0
    assert captured["files"]["file"][0] == "audio.ogg"
    assert captured["data"]["model"] == "whisper-1"
    assert captured["data"]["language"] == "es"
    assert captured["data"]["response_format"] == "verbose_json"
    assert captured["timeout"] == 120.0


def test_stt_confidence_none_without_segments(monkeypatch):
    from app.integrations import stt

    monkeypatch.setattr(
        stt, "get_settings",
        lambda: _stt_settings(stt_provider="openai", openai_api_key="sk-test"),
    )
    monkeypatch.setattr(
        stt.httpx, "post",
        lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"text": "hola"}),
    )
    result = stt.transcribe(b"bytes", "audio/mpeg")
    assert result.confidence is None


def test_stt_disabled_raises_unavailable(monkeypatch):
    from app.integrations import stt

    monkeypatch.setattr(stt, "get_settings", lambda: _stt_settings())
    with pytest.raises(stt.SttUnavailable):
        stt.transcribe(b"bytes", "audio/ogg")


def test_stt_openai_without_key_raises_unavailable(monkeypatch):
    from app.integrations import stt

    monkeypatch.setattr(stt, "get_settings", lambda: _stt_settings(stt_provider="openai"))
    with pytest.raises(stt.SttUnavailable):
        stt.transcribe(b"bytes", "audio/ogg")


def test_stt_provider_error_raises_stt_error(monkeypatch):
    from app.integrations import stt

    monkeypatch.setattr(
        stt, "get_settings",
        lambda: _stt_settings(stt_provider="openai", openai_api_key="sk-test"),
    )
    monkeypatch.setattr(
        stt.httpx, "post",
        lambda *a, **k: SimpleNamespace(status_code=500, json=lambda: {}),
    )
    with pytest.raises(stt.SttError):
        stt.transcribe(b"bytes", "audio/ogg")


# ── LLM ────────────────────────────────────────────────────────────────


def _case_payload() -> dict:
    return {
        "reporter_is_affected": {"value": True, "confidence": 0.9},
        "household": {"member_count": {"value": 4, "confidence": 0.95}},
        "location": {"municipality_text": {"value": "Manizales", "confidence": 0.8}},
        "damage_summary": {"value": "techo caído", "confidence": 0.7},
        "needs": [
            {
                "catalog_code": "WATER.BOTTLED",
                "free_text": "agua para los niños",
                "horizon": "EMERGENCY",
                "quantity": 20,
                "confidence": 0.85,
            },
            {
                "catalog_code": "CODIGO.INEXISTENTE",
                "free_text": "una carpa grande",
                "horizon": None,
                "quantity": None,
                "confidence": 0.6,
            },
        ],
        "unknowns": ["fecha exacta del daño"],
        "safety_flags": ["MINOR"],
    }


class _FakeMessages:
    def __init__(self, results):
        # results: lista de respuestas o excepciones, en orden de llamada.
        self._results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _fake_client(monkeypatch, results):
    from app.integrations import llm

    messages = _FakeMessages(results)
    monkeypatch.setattr(
        llm.anthropic, "Anthropic",
        lambda api_key: SimpleNamespace(messages=messages),
    )
    return messages


def _text_response(payload) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))]
    )


def _llm_settings(api_key="sk-ant-test"):
    return SimpleNamespace(anthropic_api_key=api_key, anthropic_model="claude-test-model")


def test_llm_extract_returns_dict(monkeypatch):
    from app.integrations import llm

    monkeypatch.setattr(llm, "get_settings", _llm_settings)
    payload = _case_payload()
    messages = _fake_client(monkeypatch, [_text_response(payload)])

    data = llm.extract("case", "necesito agua para 4 personas", ["WATER.BOTTLED"])

    assert data == payload
    call = messages.calls[0]
    assert call["model"] == "claude-test-model"
    assert call["max_tokens"] == 2048
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"] is llm.CASE_INTAKE_JSON_SCHEMA
    assert "WATER.BOTTLED" in call["system"]


def test_llm_invalid_json_raises_llm_error(monkeypatch):
    from app.integrations import llm

    monkeypatch.setattr(llm, "get_settings", _llm_settings)
    _fake_client(
        monkeypatch,
        [SimpleNamespace(content=[SimpleNamespace(type="text", text="esto no es json")])],
    )
    with pytest.raises(llm.LlmError):
        llm.extract("case", "hola", [])


def test_llm_without_api_key_raises_unavailable(monkeypatch):
    from app.integrations import llm

    monkeypatch.setattr(llm, "get_settings", lambda: _llm_settings(api_key=""))
    with pytest.raises(llm.LlmUnavailable):
        llm.extract("case", "hola", [])


def test_llm_retries_once_on_rate_limit(monkeypatch):
    import anthropic

    from app.integrations import llm

    monkeypatch.setattr(llm, "get_settings", _llm_settings)
    rate_limited = anthropic.RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=httpx.Request("POST", "https://api.test")),
        body=None,
    )
    messages = _fake_client(monkeypatch, [rate_limited, _text_response(_case_payload())])

    data = llm.extract("case", "hola", [])
    assert data["household"]["member_count"]["value"] == 4
    assert len(messages.calls) == 2


def test_llm_client_error_does_not_retry(monkeypatch):
    import anthropic

    from app.integrations import llm

    monkeypatch.setattr(llm, "get_settings", _llm_settings)
    bad_request = anthropic.APIStatusError(
        "bad request",
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.test")),
        body=None,
    )
    messages = _fake_client(monkeypatch, [bad_request])
    with pytest.raises(llm.LlmError):
        llm.extract("case", "hola", [])
    assert len(messages.calls) == 1


def test_llm_offer_schema_selected(monkeypatch):
    from app.integrations import llm

    monkeypatch.setattr(llm, "get_settings", _llm_settings)
    payload = {
        "offer_type": "DONATION",
        "items": [],
        "origin_municipality_text": None,
        "availability_text": None,
        "unknowns": [],
    }
    messages = _fake_client(monkeypatch, [_text_response(payload)])
    data = llm.extract("offer", "puedo donar agua", ["WATER.BOTTLED"])
    assert data == payload
    schema = messages.calls[0]["output_config"]["format"]["schema"]
    assert schema is llm.OFFER_INTAKE_JSON_SCHEMA


# ── Orquestación con BD ────────────────────────────────────────────────


@pytest.fixture
def seeded(db_session):
    """Incidente, canal, conversación, reporte, mensaje y catálogo mínimos."""
    from app.modules.cases.models import NeedCatalog, Report, ReporterRole, ReportStatus
    from app.modules.identity.models import ChannelType, Incident, IncidentStatus
    from app.modules.intake.models import (
        Channel,
        Conversation,
        Message,
        MessageDirection,
        MessageType,
        ProviderType,
    )

    suffix = uuid.uuid4().hex[:8].upper()
    incident = Incident(code=f"TEST-AI-{suffix}", name="Test IA", status=IncidentStatus.ACTIVE)
    db_session.add(incident)
    channel = Channel(
        type=ChannelType.WHATSAPP,
        provider=ProviderType.META_CLOUD_API,
        display_name="canal-test-ia",
    )
    db_session.add(channel)
    db_session.flush()
    conversation = Conversation(incident_id=incident.id, channel_id=channel.id)
    db_session.add(conversation)
    db_session.flush()
    report = Report(
        incident_id=incident.id,
        reporter_role=ReporterRole.SELF,
        conversation_id=conversation.id,
        narrative="Se cayó el techo, somos 4 y necesitamos agua.",
        status=ReportStatus.COLLECTING,
    )
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.INBOUND,
        type=MessageType.TEXT,
        provider_message_id=f"wamid.test.{suffix}",
    )
    db_session.add_all([report, message])
    catalog = db_session.execute(
        sa.select(NeedCatalog).where(NeedCatalog.code == "WATER.BOTTLED")
    ).scalars().first()
    if catalog is None:
        db_session.add(
            NeedCatalog(code="WATER.BOTTLED", name_es="Agua embotellada", active=True, version=1)
        )
    db_session.flush()
    return SimpleNamespace(
        incident=incident, conversation=conversation, report=report, message=message
    )


def test_case_extraction_creates_run_and_candidates(db_session, seeded, monkeypatch):
    import sqlalchemy as sa

    from app.core.security import decrypt_json
    from app.modules.intake import ai_service
    from app.modules.intake.models import (
        AiExtractionRun,
        AiRunStatus,
        AiTaskType,
        CandidateStatus,
        ExtractionCandidate,
    )

    payload = _case_payload()
    calls = []

    def fake_extract(task, text, catalog_codes, municipalities_hint=None):
        calls.append((task, catalog_codes))
        return payload

    monkeypatch.setattr(ai_service.llm, "extract", fake_extract)

    text = "Se cayó el techo, somos 4 y necesitamos agua."
    run = ai_service.run_case_extraction(db_session, seeded.report, text, seeded.message.id)

    assert run is not None
    assert run.status == AiRunStatus.SUCCEEDED
    assert run.task_type == AiTaskType.EXTRACT_CASE
    assert run.output_json_enc is not None
    assert decrypt_json(run.output_json_enc) == payload
    assert run.confidence is not None
    assert calls[0][0] == "case"
    assert "WATER.BOTTLED" in calls[0][1]

    candidates = db_session.execute(
        sa.select(ExtractionCandidate).where(ExtractionCandidate.ai_run_id == run.id)
    ).scalars().all()
    paths = {c.field_path for c in candidates}
    assert {
        "reporter_is_affected",
        "household.member_count",
        "location.municipality_text",
        "damage_summary",
        "needs[0]",
        "needs[1]",
    } <= paths
    for candidate in candidates:
        assert candidate.status == CandidateStatus.PROPOSED
        assert candidate.confidence is not None
        assert candidate.provenance_offsets == {"message_id": str(seeded.message.id)}

    by_path = {c.field_path: c for c in candidates}
    assert by_path["household.member_count"].normalized_value == {"value": 4}
    # normalización contra catálogo: código válido se conserva, inválido → null
    assert by_path["needs[0]"].normalized_value["catalog_code"] == "WATER.BOTTLED"
    assert by_path["needs[1]"].normalized_value["catalog_code"] is None
    # la vista normalizada de needs no lleva texto libre (sin PII)
    assert "free_text" not in by_path["needs[0]"].normalized_value
    assert decrypt_json(by_path["needs[0]"].proposed_value_enc)["free_text"] == (
        "agua para los niños"
    )

    # Cache §17.4: misma entrada (normalizada) no crea un segundo run
    run2 = ai_service.run_case_extraction(
        db_session, seeded.report, "  Se cayó el techo,   somos 4 y necesitamos agua. ",
        seeded.message.id,
    )
    assert run2 is not None and run2.id == run.id
    total = db_session.execute(
        sa.select(sa.func.count()).select_from(AiExtractionRun).where(
            AiExtractionRun.task_type == AiTaskType.EXTRACT_CASE,
            AiExtractionRun.message_id == seeded.message.id,
        )
    ).scalar_one()
    assert total == 1
    assert len(calls) == 1


def test_llm_unavailable_creates_fallback_queue_item(db_session, seeded, monkeypatch):
    import sqlalchemy as sa

    from app.integrations.llm import LlmUnavailable
    from app.modules.intake import ai_service
    from app.modules.intake.models import AgentQueueItem, AiRunStatus, QueuePriority

    def boom(*args, **kwargs):
        raise LlmUnavailable("sin api key")

    monkeypatch.setattr(ai_service.llm, "extract", boom)

    run = ai_service.run_case_extraction(
        db_session, seeded.report, "necesitamos ayuda urgente", seeded.message.id
    )
    assert run is not None
    assert run.status == AiRunStatus.FAILED

    item = db_session.execute(
        sa.select(AgentQueueItem).where(
            AgentQueueItem.conversation_id == seeded.conversation.id,
            AgentQueueItem.queue_code == "AI_FALLBACK",
        )
    ).scalars().one()
    assert item.priority == QueuePriority.P2
    assert item.reason_code == "LLM_UNAVAILABLE"


def test_report_submitted_handler_runs_extraction(db_session, seeded, monkeypatch):
    import sqlalchemy as sa

    from app.modules.intake import ai_service
    from app.modules.intake.models import AiExtractionRun, AiRunStatus, AiTaskType

    monkeypatch.setattr(
        ai_service.llm, "extract", lambda *a, **k: _case_payload()
    )
    event = SimpleNamespace(aggregate_id=seeded.report.id)
    ai_service.handle_report_submitted(db_session, event)

    run = db_session.execute(
        sa.select(AiExtractionRun).where(
            AiExtractionRun.message_id == seeded.message.id,
            AiExtractionRun.task_type == AiTaskType.EXTRACT_CASE,
        )
    ).scalars().one()
    assert run.status == AiRunStatus.SUCCEEDED
