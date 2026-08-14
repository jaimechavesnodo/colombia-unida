"""Orquestación de IA sobre el intake (§9.1, handlers de outbox).

- media.ready (audio): transcribe con STT, anexa la transcripción al
  Report activo de la conversación y dispara extracción de caso.
- report.submitted: extracción de caso sobre la narrativa.

Toda inferencia queda registrada en AiExtractionRun (reproducible, con
cache por hash de entrada §17.4) y sus campos propuestos en
ExtractionCandidate (PROPOSED hasta confirmación humana, §9.1).

Si la IA no está disponible, la captura manual sigue (criterio AI-02):
run FAILED + AgentQueueItem en la cola AI_FALLBACK; nunca se propaga el
error al outbox como fallo permanente.

Prohibido loggear transcripciones, narrativas o salidas del modelo.
"""

import hashlib
import logging
import time

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import log_ctx
from app.core.outbox import OutboxEvent, publish, register_handler
from app.core.security import decrypt_json, encrypt_json
from app.integrations import llm, stt
from app.integrations.llm import LlmError, LlmUnavailable
from app.integrations.stt import SttError, SttUnavailable
from app.modules.cases.models import NeedCatalog, Report
from app.modules.intake.models import (
    AgentQueueItem,
    AiExtractionRun,
    AiRunStatus,
    AiTaskType,
    CandidateStatus,
    ExtractionCandidate,
    MediaAsset,
    Message,
    MessageDirection,
    MessageProcessingStatus,
    QueuePriority,
)

logger = logging.getLogger("intake.ai")

TRANSCRIBE_PROMPT_VERSION = "v1"
AI_FALLBACK_QUEUE = "AI_FALLBACK"


# ── Utilidades ─────────────────────────────────────────────────────────


def _find_succeeded_run(
    session: Session,
    input_hash: bytes,
    task_type: AiTaskType,
    model_version: str,
    prompt_version: str,
) -> AiExtractionRun | None:
    """Cache seguro de inferencias: UNIQUE(input_hash, task, model, prompt)."""
    return session.execute(
        sa.select(AiExtractionRun).where(
            AiExtractionRun.input_hash == input_hash,
            AiExtractionRun.task_type == task_type,
            AiExtractionRun.model_version == model_version,
            AiExtractionRun.prompt_version == prompt_version,
            AiExtractionRun.status == AiRunStatus.SUCCEEDED,
        )
    ).scalar_one_or_none()


def _manual_fallback(session: Session, conversation_id, reason_code: str) -> None:
    """AI-02: la IA falla, la captura manual sigue — cola de agentes."""
    session.add(
        AgentQueueItem(
            conversation_id=conversation_id,
            queue_code=AI_FALLBACK_QUEUE,
            priority=QueuePriority.P2,
            reason_code=reason_code,
        )
    )
    log_ctx(logger, logging.WARNING, "ai fallback queued", reason=reason_code)


def _load_media_bytes(media: MediaAsset) -> bytes:
    # Import perezoso: el módulo storage se construye en paralelo.
    from app.integrations.storage import get_storage, parse_uri

    bucket, key = parse_uri(media.object_uri)
    return get_storage().get(bucket, key)


def _active_report(session: Session, conversation_id) -> Report | None:
    if conversation_id is None:
        return None
    return session.execute(
        sa.select(Report)
        .where(Report.conversation_id == conversation_id)
        .order_by(Report.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _active_catalog_codes(session: Session) -> list[str]:
    rows = session.execute(
        sa.select(NeedCatalog.code)
        .where(NeedCatalog.active.is_(True))
        .order_by(NeedCatalog.code)
    ).all()
    return [r[0] for r in rows]


# ── Extracción de caso ─────────────────────────────────────────────────


def _add_candidate(
    session: Session,
    run: AiExtractionRun,
    field_path: str,
    proposed: dict,
    normalized: dict | None,
    confidence,
    provenance: dict,
) -> None:
    session.add(
        ExtractionCandidate(
            ai_run_id=run.id,
            target_entity_type="report",
            field_path=field_path,
            proposed_value_enc=encrypt_json(proposed),
            # Vista operativa sin PII: solo códigos/cantidades normalizadas.
            normalized_value=normalized,
            confidence=confidence,
            provenance_offsets=provenance,
            status=CandidateStatus.PROPOSED,
        )
    )


def _normalize_municipality(session: Session, text: str) -> dict | None:
    from app.modules.intake.engine import _find_municipality

    row = _find_municipality(session, text)
    if row is None:
        return None
    return {
        "municipality_code": row.municipality_code,
        "municipality_name": row.municipality_name,
    }


def _create_candidates(
    session: Session, run: AiExtractionRun, data: dict, provenance: dict, valid_codes: set[str]
) -> list[float]:
    confidences: list[float] = []

    def scalar(field_path: str, node, normalized=None):
        if not isinstance(node, dict) or node.get("value") is None:
            return
        conf = node.get("confidence")
        _add_candidate(session, run, field_path, node, normalized, conf, provenance)
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))

    scalar(
        "reporter_is_affected",
        data.get("reporter_is_affected"),
        {"value": (data.get("reporter_is_affected") or {}).get("value")},
    )
    household = data.get("household") or {}
    member_count = household.get("member_count")
    scalar(
        "household.member_count",
        member_count,
        {"value": (member_count or {}).get("value")} if member_count else None,
    )
    location = data.get("location") or {}
    muni = location.get("municipality_text")
    if isinstance(muni, dict) and muni.get("value"):
        scalar(
            "location.municipality_text",
            muni,
            _normalize_municipality(session, muni["value"]),
        )
    # narrativa sensible: sin vista normalizada (solo cifrado)
    scalar("damage_summary", data.get("damage_summary"), None)

    for i, need in enumerate(data.get("needs") or []):
        if not isinstance(need, dict):
            continue
        code = need.get("catalog_code")
        conf = need.get("confidence")
        normalized = {
            # normalización contra need_catalog: código inválido → null
            "catalog_code": code if code in valid_codes else None,
            "horizon": need.get("horizon"),
            "quantity": need.get("quantity"),
        }
        _add_candidate(session, run, f"needs[{i}]", need, normalized, conf, provenance)
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))
    return confidences


def run_case_extraction(
    session: Session, report: Report, text: str, message_id
) -> AiExtractionRun | None:
    """Extrae campos del caso desde `text` con cache por hash de entrada."""
    normalized_text = " ".join(text.split()).strip()
    if not normalized_text:
        return None
    input_hash = hashlib.sha256(normalized_text.encode("utf-8")).digest()
    settings = get_settings()
    model = settings.anthropic_model
    prompt_version = f"{llm.CASE_INTAKE_SCHEMA_VERSION}:{llm.PROMPT_VERSION}"

    cached = _find_succeeded_run(
        session, input_hash, AiTaskType.EXTRACT_CASE, model, prompt_version
    )
    if cached is not None:
        return cached  # §17.4: no repetir inferencia sobre la misma entrada

    run = AiExtractionRun(
        message_id=message_id,
        task_type=AiTaskType.EXTRACT_CASE,
        provider="anthropic",
        model_version=model,
        prompt_version=prompt_version,
        input_hash=input_hash,
        status=AiRunStatus.RUNNING,
    )
    session.add(run)
    session.flush()

    catalog_codes = _active_catalog_codes(session)
    started = time.monotonic()
    try:
        data = llm.extract("case", text, catalog_codes)
    except LlmUnavailable:
        run.status = AiRunStatus.FAILED
        run.latency_ms = int((time.monotonic() - started) * 1000)
        _manual_fallback(session, report.conversation_id, "LLM_UNAVAILABLE")
        return run
    except LlmError:
        run.status = AiRunStatus.FAILED
        run.latency_ms = int((time.monotonic() - started) * 1000)
        _manual_fallback(session, report.conversation_id, "LLM_ERROR")
        return run

    run.latency_ms = int((time.monotonic() - started) * 1000)
    run.output_json_enc = encrypt_json(data)
    run.safety_flags = {"flags": data.get("safety_flags") or []}

    provenance = (
        {"message_id": str(message_id)} if message_id else {"report_id": str(report.id)}
    )
    confidences = _create_candidates(session, run, data, provenance, set(catalog_codes))
    run.confidence = sum(confidences) / len(confidences) if confidences else None
    run.status = AiRunStatus.SUCCEEDED
    # §9.1-7: pedir confirmación al reportante (confirmation_service escucha)
    publish(
        session,
        event_type="ai.extraction.completed",
        aggregate_type="ai_run",
        aggregate_id=run.id,
    )
    log_ctx(
        logger, logging.INFO, "case extraction done",
        task="EXTRACT_CASE", model=model, latency_ms=run.latency_ms,
        status=run.status.value, candidates=len(confidences),
    )
    return run


# ── Handlers de outbox ─────────────────────────────────────────────────


def handle_media_ready(session: Session, event: OutboxEvent) -> None:
    """media.ready (aggregate=media_asset): transcripción + extracción."""
    media = session.get(MediaAsset, event.aggregate_id)
    if media is None or not (media.mime_type or "").startswith("audio"):
        return
    message = session.get(Message, media.message_id)
    if message is None:
        return

    settings = get_settings()
    provider = (settings.stt_provider or "disabled").lower()
    model = stt.OPENAI_STT_MODEL if provider == "openai" else provider

    # AI-02: sin STT no se toca storage ni se crea run; captura manual.
    if provider == "disabled" or (provider == "openai" and not settings.openai_api_key):
        message.processing_status = MessageProcessingStatus.NEEDS_REVIEW
        _manual_fallback(session, message.conversation_id, "STT_UNAVAILABLE")
        return

    input_hash = bytes(media.sha256) if media.sha256 else None
    audio: bytes | None = None
    if input_hash is None:
        audio = _load_media_bytes(media)
        input_hash = hashlib.sha256(audio).digest()

    cached = _find_succeeded_run(
        session, input_hash, AiTaskType.TRANSCRIBE, model, TRANSCRIBE_PROMPT_VERSION
    )
    if cached is not None:
        text = (decrypt_json(cached.output_json_enc) or {}).get("text") or ""
    else:
        if audio is None:
            audio = _load_media_bytes(media)
        run = AiExtractionRun(
            message_id=message.id,
            task_type=AiTaskType.TRANSCRIBE,
            provider=provider,
            model_version=model,
            prompt_version=TRANSCRIBE_PROMPT_VERSION,
            input_hash=input_hash,
            status=AiRunStatus.RUNNING,
        )
        session.add(run)
        session.flush()
        started = time.monotonic()
        try:
            result = stt.transcribe(audio, media.mime_type)
        except SttUnavailable:
            run.status = AiRunStatus.FAILED
            run.latency_ms = int((time.monotonic() - started) * 1000)
            message.processing_status = MessageProcessingStatus.NEEDS_REVIEW
            _manual_fallback(session, message.conversation_id, "STT_UNAVAILABLE")
            return
        except SttError:
            run.status = AiRunStatus.FAILED
            run.latency_ms = int((time.monotonic() - started) * 1000)
            message.processing_status = MessageProcessingStatus.NEEDS_REVIEW
            _manual_fallback(session, message.conversation_id, "STT_FAILED")
            return
        run.status = AiRunStatus.SUCCEEDED
        run.latency_ms = int((time.monotonic() - started) * 1000)
        run.output_json_enc = encrypt_json(
            {"text": result.text, "language": result.language}
        )
        run.confidence = result.confidence
        media.transcript_id = run.id
        text = result.text

    if not text:
        return
    report = _active_report(session, message.conversation_id)
    if report is not None:
        # Idempotente ante reintentos del outbox: no duplicar el anexo.
        snippet = "[audio] " + text
        if snippet not in (report.narrative or ""):
            report.narrative = ((report.narrative or "") + "\n" + snippet).strip()
        run_case_extraction(session, report, text, message.id)
    message.processing_status = MessageProcessingStatus.PROCESSED


def handle_report_submitted(session: Session, event: OutboxEvent) -> None:
    """report.submitted: extracción de caso sobre la narrativa completa."""
    report = session.get(Report, event.aggregate_id)
    if report is None or not report.narrative:
        return
    if report.conversation_id is None:
        return
    # El run requiere un mensaje ancla (FK NOT NULL): último inbound.
    message_id = session.execute(
        sa.select(Message.id)
        .where(
            Message.conversation_id == report.conversation_id,
            Message.direction == MessageDirection.INBOUND,
        )
        .order_by(Message.received_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if message_id is None:
        return
    run_case_extraction(session, report, report.narrative, message_id)


def register() -> None:
    register_handler("media.ready", handle_media_ready)
    register_handler("report.submitted", handle_report_submitted)
