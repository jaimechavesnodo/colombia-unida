"""Confirmación humana de candidatos de IA (§9.1-7/8 del alcance).

Cuando la extracción termina, se envía por WhatsApp un resumen corto de
los campos materiales propuestos y se pide "1 Sí / 2 Corregir / 3
Completar después". La aceptación crea human_confirmations y promueve
los datos en una transacción; nunca se sobrescribe el original.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import log_ctx
from app.core.model_base import utcnow
from app.core.outbox import OutboxEvent, publish, register_handler
from app.core.security import decrypt_text, encrypt_json
from app.modules.cases.models import (
    Case,
    Need,
    NeedCatalog,
    NeedHorizon,
    NeedStatus,
    Report,
)
from app.modules.identity.models import GeoDivipola, Location, LocationSource, PersonIdentifier
from app.modules.intake.models import (
    AgentMode,
    AiExtractionRun,
    CandidateStatus,
    ConfirmationDecision,
    Conversation,
    ExtractionCandidate,
    HumanConfirmation,
    Message,
)

logger = logging.getLogger("confirmation")

EVENT_EXTRACTION_COMPLETED = "ai.extraction.completed"


def _summary_from_candidates(session: Session, candidates: list[ExtractionCandidate]) -> str:
    lines = []
    for c in candidates:
        nv = c.normalized_value or {}
        if c.field_path.startswith("needs["):
            qty = nv.get("quantity")
            label = nv.get("catalog_name") or nv.get("catalog_code") or nv.get("free_text", "")
            qty_txt = f"{qty:g} × " if isinstance(qty, int | float) else ""
            lines.append(f"• Necesitan: {qty_txt}{label}")
        elif c.field_path == "household.member_count":
            lines.append(f"• Personas en el hogar: {nv.get('value')}")
        elif c.field_path == "location.municipality_text":
            lines.append(f"• Municipio: {nv.get('value')}")
        elif c.field_path == "damage_summary":
            lines.append(f"• Daño: {str(nv.get('value'))[:120]}")
    return "\n".join(lines) if lines else "• (sin datos claros)"


def send_confirmation_prompt(session: Session, event: OutboxEvent) -> None:
    """Handler de ai.extraction.completed: resume y pregunta al reportante."""
    run = session.get(AiExtractionRun, event.aggregate_id)
    if run is None:
        return
    candidates = (
        session.execute(
            sa.select(ExtractionCandidate).where(
                ExtractionCandidate.ai_run_id == run.id,
                ExtractionCandidate.status == CandidateStatus.PROPOSED,
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return

    message = session.get(Message, run.message_id) if run.message_id else None
    if message is None:
        return
    conversation = session.get(Conversation, message.conversation_id)
    if conversation is None or conversation.agent_mode == AgentMode.HUMAN:
        return  # en modo humano el agente revisa desde la consola

    # Estado del flujo: esperar confirmación de IA
    from app.modules.intake import engine as bot_engine

    state = bot_engine._load_state(conversation)
    state["flow"] = "AWAIT_AI_CONFIRM"
    state["ai_run_id"] = str(run.id)
    bot_engine._save_state(conversation, state)

    summary = _summary_from_candidates(session, list(candidates))
    text = (
        f"Esto fue lo que entendimos:\n{summary}\n\n"
        "*1* Sí, es correcto\n*2* Corregir algo\n*3* Completar después"
    )
    _send_to_conversation(session, conversation, text, "AI_CONFIRM_V1")


def _send_to_conversation(session, conversation, text: str, prompt_code: str) -> None:
    """Envía por Graph API (si hay credenciales) y persiste el saliente."""
    from app.integrations.meta_whatsapp.client import GraphClient
    from app.modules.intake.service import _record_outbound

    settings = get_settings()
    provider_id = None
    if settings.meta_access_token and settings.meta_phone_number_id:
        wa_id = _wa_id_for_conversation(session, conversation)
        if wa_id:
            try:
                provider_id = GraphClient().send_text(wa_id, text)
            except Exception:
                log_ctx(logger, logging.ERROR, "send failed", prompt_code=prompt_code)
    _record_outbound(session, conversation, text, provider_id)


def _wa_id_for_conversation(session, conversation) -> str | None:
    """Recupera el teléfono descifrando el identificador del participante."""
    row = session.execute(
        sa.select(PersonIdentifier.value_enc)
        .where(PersonIdentifier.value_hmac.isnot(None))
        .where(PersonIdentifier.value_hmac == conversation.external_thread_key_hmac)
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return decrypt_text(row)
    except Exception:
        return None


def handle_confirmation_reply(
    session: Session, conversation, person_id, message, reply: str
) -> str:
    """Procesa la respuesta 1/2/3 al resumen de IA. Devuelve texto de respuesta."""
    from app.modules.intake import engine as bot_engine

    state = bot_engine._load_state(conversation)
    run_id = state.get("ai_run_id")
    candidates = []
    if run_id:
        candidates = (
            session.execute(
                sa.select(ExtractionCandidate).where(
                    ExtractionCandidate.ai_run_id == run_id,
                    ExtractionCandidate.status == CandidateStatus.PROPOSED,
                )
            )
            .scalars()
            .all()
        )

    lower = reply.strip().lower()
    if lower.startswith("1") or lower in {"sí", "si", "correcto"}:
        promoted = promote_candidates(
            session, conversation, list(candidates), person_id, message.id
        )
        state["flow"] = "IDLE_SUBMITTED"
        bot_engine._save_state(conversation, state)
        code = state.get("case_code", "")
        return (
            f"¡Gracias! ✅ Registramos {promoted} dato(s) del caso *{code}*. "
            "Un validador lo revisará pronto; te avisamos por aquí."
        )
    if lower.startswith("2"):
        for c in candidates:
            _decide(session, c, person_id, message.id, ConfirmationDecision.REJECT)
            c.status = CandidateStatus.REJECTED
        state["flow"] = "COLLECTING_NARRATIVE"
        bot_engine._save_state(conversation, state)
        return (
            "Sin problema. Cuéntanos de nuevo qué pasó y qué necesitan "
            "(o solo lo que quieras corregir)."
        )
    # 3 / otro → diferir
    for c in candidates:
        _decide(session, c, person_id, message.id, ConfirmationDecision.CANNOT_CONFIRM)
        c.status = CandidateStatus.DEFERRED
    state["flow"] = "IDLE_SUBMITTED"
    bot_engine._save_state(conversation, state)
    return (
        "Guardado. 📌 Cuando puedas, escríbenos para confirmar los datos — "
        "no se pierde nada."
    )


def _decide(session, candidate, person_id, message_id, decision) -> None:
    session.add(
        HumanConfirmation(
            candidate_id=candidate.id,
            actor_person_id=person_id,
            decision=decision,
            confirmation_message_id=message_id,
            decided_at=utcnow(),
        )
    )


def promote_candidates(
    session: Session, conversation, candidates: list[ExtractionCandidate],
    person_id, message_id,
) -> int:
    """Promoción transaccional (§9.1-8): confirmaciones + datos de dominio."""
    case = session.get(Case, conversation.active_case_id) if conversation.active_case_id else None
    report = None
    if case is not None:
        report = session.execute(
            sa.select(Report)
            .where(Report.conversation_id == conversation.id)
            .order_by(Report.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    promoted = 0
    for c in candidates:
        _decide(session, c, person_id, message_id, ConfirmationDecision.ACCEPT)
        c.status = CandidateStatus.CONFIRMED
        nv = c.normalized_value or {}

        if c.field_path.startswith("needs[") and case is not None:
            catalog = None
            if nv.get("catalog_code"):
                catalog = session.execute(
                    sa.select(NeedCatalog)
                    .where(NeedCatalog.code == nv["catalog_code"], NeedCatalog.active.is_(True))
                    .order_by(NeedCatalog.version.desc())
                    .limit(1)
                ).scalar_one_or_none()
            horizon = nv.get("horizon")
            need = Need(
                case_id=case.id,
                catalog_id=catalog.id if catalog else None,
                horizon=(
                    NeedHorizon(horizon)
                    if horizon
                    else (catalog.default_horizon if catalog else NeedHorizon.EMERGENCY)
                ),
                status=NeedStatus.REPORTED,
                requested_qty=nv.get("quantity"),
                unit_code=catalog.unit_code if catalog else None,
                description_redacted=(nv.get("free_text") or "")[:280] or None,
                attributes_enc=encrypt_json({"source_candidate": str(c.id)}),
            )
            session.add(need)
            session.flush()
            publish(
                session, event_type="need.created", aggregate_type="need",
                aggregate_id=need.id,
            )
            promoted += 1

        elif c.field_path == "household.member_count" and case is not None:
            if case.household_id:
                from app.modules.identity.models import Household

                household = session.get(Household, case.household_id)
                if household is not None and household.member_count is None:
                    household.member_count = int(nv.get("value") or 0) or None
            promoted += 1

        elif c.field_path == "location.municipality_text" and report is not None:
            if report.location_id is None and nv.get("value"):
                clean = str(nv["value"]).strip().upper()
                muni = session.execute(
                    sa.select(GeoDivipola)
                    .where(GeoDivipola.municipality_name.ilike(f"{clean}%"))
                    .limit(1)
                ).scalar_one_or_none()
                if muni:
                    loc = Location(
                        admin1=muni.department_name,
                        admin2=muni.municipality_name,
                        source=LocationSource.GEOCODED_TEXT,
                    )
                    session.add(loc)
                    session.flush()
                    report.location_id = loc.id
            promoted += 1
        else:
            promoted += 1

    return promoted


def register() -> None:
    register_handler(EVENT_EXTRACTION_COMPLETED, send_confirmation_prompt)
