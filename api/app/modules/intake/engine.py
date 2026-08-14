"""Motor conversacional del bot (§4.3–4.4, §5.1 del alcance).

Contrato de baja conectividad: asíncrono (el draft sobrevive horas o
días), una pregunta material por turno, respuestas numeradas, medios
diferidos, "completar después" siempre disponible. El estado del flujo
persiste cifrado en conversations.context_summary_enc; la verdad de
negocio vive en reports/cases/needs, nunca solo en el estado del flujo.
"""

import logging
import re
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.ids import new_short_code
from app.core.model_base import utcnow
from app.core.outbox import publish
from app.core.security import decrypt_json, encrypt_json
from app.modules.cases.models import (
    Case,
    CasePerson,
    CasePersonRole,
    CaseStatus,
    Report,
    ReporterRole,
    ReportStatus,
)
from app.modules.identity.models import (
    ChannelType,
    Consent,
    ConsentPurpose,
    ConsentStatus,
    GeoDivipola,
    Household,
    Location,
    LocationSource,
)
from app.modules.intake.models import (
    AgentMode,
    AgentQueueItem,
    Conversation,
    ConversationIntent,
    Message,
    QueueItemStatus,
    QueuePriority,
)
from app.modules.supply.models import (
    OfferStatus,
    ResourceOffer,
    ResourceType,
)

logger = logging.getLogger("engine")

NOTICE_VERSION = "aviso-v1-borrador"  # ⚠️ texto legal pendiente de revisión jurídica

# ── Prompts (last_safe_prompt_code §4.4) ───────────────────────────────

PROMPTS = {
    "WELCOME_V1": (
        "Hola 👋 Soy el asistente de *Colombia Unida*, apoyo humanitario tras el "
        "terremoto del 10 de agosto.\n\n"
        "¿Qué necesitas?\n"
        "*1* Necesito ayuda\n"
        "*2* Reportar por otra familia\n"
        "*3* Quiero ayudar / donar\n"
        "*4* Consultar un caso\n\n"
        "Responde con el número. Escribe *AGENTE* en cualquier momento para "
        "hablar con una persona."
    ),
    "CONSENT_V1": (
        "Para registrar tu solicitud necesitamos tratar tus datos (nombre, "
        "ubicación y lo que nos cuentes) solo para coordinar la ayuda. "
        "Nunca publicaremos tu información sin tu permiso.\n\n"
        "*1* Acepto\n*2* No acepto\n\n"
        "Puedes pedir que borremos tus datos cuando quieras."
    ),
    "CONSENT_DENIED_V1": (
        "Entendido, no registraremos tus datos. Si cambias de opinión, "
        "escríbenos de nuevo. Si es una emergencia vital, llama a la línea 123."
    ),
    "ASK_NARRATIVE_V1": (
        "Tu caso quedó registrado con el código *{code}*. Guárdalo.\n\n"
        "Ahora cuéntanos qué pasó y qué necesitan: puedes escribir o enviar "
        "un *audio* 🎙️. No importa si la señal va y viene, seguimos cuando "
        "puedas."
    ),
    "ACK_NARRATIVE_V1": (
        "Gracias, quedó guardado. ✅\n\n¿En qué *municipio* está la familia "
        "afectada? (ej: Manizales, Chinchiná)"
    ),
    "ACK_AUDIO_V1": (
        "Recibimos tu audio 🎙️, lo vamos a escuchar con cuidado.\n\n"
        "¿En qué *municipio* está la familia afectada?"
    ),
    "ASK_LOCATION_RETRY_V1": (
        "No encontré ese municipio. Escríbelo de nuevo (solo el nombre, "
        "ej: Manizales) o *3* para completar después."
    ),
    "ASK_HOUSEHOLD_V1": (
        "¿Cuántas personas viven en el hogar afectado? Responde con un "
        "número (ej: 5)."
    ),
    "SUMMARY_V1": (
        "Esto es lo que tenemos del caso *{code}*:\n{summary}\n\n"
        "*1* Enviar solicitud\n*2* Corregir algo\n*3* Completar después"
    ),
    "SUBMITTED_V1": (
        "✅ Tu solicitud quedó enviada con el código *{code}*.\n\n"
        "Un validador la revisará pronto. Te escribiremos por aquí. "
        "Puedes consultar el estado cuando quieras enviando el código."
    ),
    "SAVED_INCOMPLETE_V1": (
        "Guardamos lo que llevas del caso *{code}*. 📌\n\n"
        "Cuando puedas, escríbenos para completarlo — no se pierde nada."
    ),
    "ASK_CORRECTION_V1": (
        "Dinos qué quieres corregir (ubicación, personas, o vuelve a contar "
        "lo que pasó) y lo actualizamos."
    ),
    "ASK_OFFER_TYPE_V1": (
        "¡Gracias por querer ayudar! 💛 ¿Qué tipo de ayuda ofreces?\n\n"
        "*1* Dinero\n*2* Cosas (mercados, colchones, ropa…)\n"
        "*3* Servicios profesionales\n*4* Transporte\n*5* Voluntariado"
    ),
    "ASK_OFFER_DETAIL_V1": (
        "Cuéntanos qué ofreces, cuánto y desde dónde (puedes escribir o "
        "enviar audio). Ej: \"10 colchones nuevos en Bogotá, puedo "
        "entregarlos el sábado\"."
    ),
    "OFFER_THANKS_V1": (
        "¡Mil gracias! 🙏 Registramos tu oferta con el código *{code}*. "
        "Nuestro equipo la revisará y te escribirá por aquí para coordinar."
    ),
    "ASK_CASE_CODE_V1": "Escribe el código del caso (ej: CU-7K2M9P).",
    "CASE_STATUS_V1": "El caso *{code}* está en estado: {status}.\n{detail}",
    "CASE_NOT_FOUND_V1": (
        "No encontré un caso con ese código. Revisa que esté bien escrito "
        "(ej: CU-7K2M9P)."
    ),
    "HANDOFF_V1": (
        "Te vamos a conectar con una persona del equipo. 🧑‍💼 "
        "Te escribirá por aquí lo antes posible."
    ),
    "FALLBACK_V1": (
        "No te entendí. Responde con el número de una opción, o escribe "
        "*AGENTE* para hablar con una persona."
    ),
}

CASE_STATUS_ES = {
    "DRAFT": "en registro",
    "INCOMPLETE": "registrado, faltan datos",
    "PENDING_VERIFICATION": "en verificación",
    "VERIFIED": "verificado",
    "ACTIVE": "activo, buscando ayuda",
    "PARTIALLY_SERVED": "recibiendo ayuda (parcial)",
    "SERVED": "ayuda entregada",
    "CLOSED": "cerrado",
    "ON_HOLD": "en pausa",
}

CASE_CODE_RE = re.compile(r"\bCU-[2-9A-HJKMNP-Z]{6}\b", re.IGNORECASE)


@dataclass
class Reply:
    prompt_code: str
    text: str


@dataclass
class EngineResult:
    replies: list[Reply] = field(default_factory=list)
    handoff: bool = False


def _load_state(conversation: Conversation) -> dict:
    if conversation.context_summary_enc:
        try:
            return decrypt_json(conversation.context_summary_enc)
        except Exception:  # clave rotada o dato corrupto: reiniciar flujo con cortesía
            logger.warning("estado conversacional ilegible; se reinicia")
    return {"flow": "NEW"}


def _save_state(conversation: Conversation, state: dict) -> None:
    conversation.context_summary_enc = encrypt_json(state)


def _reply(prompt_code: str, **fmt) -> Reply:
    return Reply(prompt_code=prompt_code, text=PROMPTS[prompt_code].format(**fmt))


def _grant_consents(session: Session, person_id, incident_id, message_id) -> None:
    for purpose in (
        ConsentPurpose.CASE_MANAGEMENT,
        ConsentPurpose.CONTACT,
        ConsentPurpose.AI_PROCESSING,
    ):
        session.add(
            Consent(
                person_id=person_id,
                incident_id=incident_id,
                purpose=purpose,
                notice_version=NOTICE_VERSION,
                status=ConsentStatus.GRANTED,
                captured_via=ChannelType.WHATSAPP,
                proof_message_id=message_id,
                granted_at=utcnow(),
            )
        )


def _create_case_and_report(
    session: Session, conversation: Conversation, person_id, intent: ConversationIntent
) -> tuple[Case, Report]:
    case = Case(
        incident_id=conversation.incident_id,
        case_code=new_short_code("CU"),
        case_type="HOUSEHOLD",
        status=CaseStatus.DRAFT,
    )
    session.add(case)
    session.flush()
    report = Report(
        incident_id=conversation.incident_id,
        reporter_person_id=person_id,
        reporter_role=(
            ReporterRole.SELF
            if intent == ConversationIntent.NEED_HELP
            else ReporterRole.THIRD_PARTY
        ),
        channel_id=conversation.channel_id,
        conversation_id=conversation.id,
        status=ReportStatus.COLLECTING,
    )
    session.add(report)
    session.flush()
    session.add(
        CasePerson(
            case_id=case.id,
            person_id=person_id,
            role=(
                CasePersonRole.AFFECTED
                if intent == ConversationIntent.NEED_HELP
                else CasePersonRole.REPORTER
            ),
            is_primary=True,
            valid_from=utcnow(),
        )
    )
    conversation.active_case_id = case.id
    publish(
        session, event_type="report.created", aggregate_type="report", aggregate_id=report.id
    )
    publish(session, event_type="case.created", aggregate_type="case", aggregate_id=case.id)
    return case, report


def _summary_text(session: Session, state: dict) -> str:
    parts = []
    if state.get("narrative_saved"):
        parts.append("• Nos contaste qué pasó ✍️")
    if state.get("has_audio"):
        parts.append("• Recibimos tu audio 🎙️")
    if state.get("municipality"):
        parts.append(f"• Municipio: {state['municipality']}")
    if state.get("household_size"):
        parts.append(f"• Personas en el hogar: {state['household_size']}")
    missing = []
    if not state.get("municipality"):
        missing.append("municipio")
    if not state.get("household_size"):
        missing.append("personas del hogar")
    if missing:
        parts.append(f"• Falta: {', '.join(missing)}")
    return "\n".join(parts) if parts else "• Aún no tenemos datos."


def _find_municipality(session: Session, text: str) -> GeoDivipola | None:
    clean = text.strip().strip(".,!?").upper()
    if not clean or len(clean) < 3:
        return None
    return session.execute(
        sa.select(GeoDivipola)
        .where(sa.func.upper(GeoDivipola.municipality_name) == clean)
        .limit(1)
    ).scalar_one_or_none() or session.execute(
        sa.select(GeoDivipola)
        .where(GeoDivipola.municipality_name.ilike(f"{clean}%"))
        .order_by(GeoDivipola.municipality_code)
        .limit(1)
    ).scalar_one_or_none()


def _submit_report(session: Session, state: dict, conversation: Conversation) -> Reply:
    report = session.get(Report, state["report_id"]) if state.get("report_id") else None
    case = session.get(Case, state["case_id"]) if state.get("case_id") else None
    complete = bool(state.get("municipality") and state.get("household_size"))
    if report:
        report.status = ReportStatus.SUBMITTED if complete else ReportStatus.SUBMITTED_INCOMPLETE
        report.submitted_at = utcnow()
        publish(
            session,
            event_type="report.submitted",
            aggregate_type="report",
            aggregate_id=report.id,
            payload={"complete": complete},
        )
    if case:
        case.status = CaseStatus.PENDING_VERIFICATION if complete else CaseStatus.INCOMPLETE
        if not complete:
            publish(
                session, event_type="case.incomplete", aggregate_type="case",
                aggregate_id=case.id,
            )
    state["flow"] = "IDLE_SUBMITTED"
    code = state.get("case_code", "")
    if complete:
        return _reply("SUBMITTED_V1", code=code)
    return _reply("SAVED_INCOMPLETE_V1", code=code)


def handle_message(
    session: Session,
    conversation: Conversation,
    person_id,
    message: Message,
) -> EngineResult:
    """Procesa un mensaje entrante en modo BOT y devuelve las respuestas.

    En modo HUMAN el bot no envía mensajes de dominio (§6.2).
    """
    result = EngineResult()
    if conversation.agent_mode == AgentMode.HUMAN:
        return result

    state = _load_state(conversation)
    text = (message.normalized_text_redacted or "").strip()
    lower = text.lower()
    is_audio = message.type.value == "AUDIO"
    is_location = message.type.value == "LOCATION"

    # Comandos globales
    if lower in {"agente", "asesor", "humano", "persona"}:
        conversation.agent_mode = AgentMode.HUMAN
        session.add(
            AgentQueueItem(
                conversation_id=conversation.id,
                case_id=conversation.active_case_id,
                queue_code="GENERAL",
                priority=QueuePriority.P2,
                reason_code="USER_REQUESTED_AGENT",
                status=QueueItemStatus.WAITING,
            )
        )
        result.handoff = True
        result.replies.append(_reply("HANDOFF_V1"))
        _save_state(conversation, state)
        return result

    code_match = CASE_CODE_RE.search(text)
    if code_match and state.get("flow") not in {"COLLECTING_NARRATIVE"}:
        case = session.execute(
            sa.select(Case).where(Case.case_code == code_match.group(0).upper())
        ).scalar_one_or_none()
        if case:
            status_es = CASE_STATUS_ES.get(case.status.value, case.status.value.lower())
            result.replies.append(
                _reply(
                    "CASE_STATUS_V1",
                    code=case.case_code,
                    status=status_es,
                    detail="Gracias por consultar; te avisaremos ante cualquier avance.",
                )
            )
        else:
            result.replies.append(_reply("CASE_NOT_FOUND_V1"))
        _save_state(conversation, state)
        return result

    flow = state.get("flow", "NEW")

    if flow == "AWAIT_AI_CONFIRM":
        from app.modules.intake.confirmation_service import handle_confirmation_reply

        reply_text = handle_confirmation_reply(session, conversation, person_id, message, text)
        result.replies.append(Reply(prompt_code="AI_CONFIRM_REPLY_V1", text=reply_text))
        return result

    if flow in {"NEW", "IDLE_SUBMITTED"}:
        result.replies.append(_reply("WELCOME_V1"))
        state["flow"] = "AWAIT_INTENT"

    elif flow == "AWAIT_INTENT":
        if lower.startswith("1") or "necesito" in lower:
            state["intent"] = ConversationIntent.NEED_HELP.value
            conversation.primary_intent = ConversationIntent.NEED_HELP
            state["flow"] = "AWAIT_CONSENT"
            result.replies.append(_reply("CONSENT_V1"))
        elif lower.startswith("2") or "otra familia" in lower or "reportar" in lower:
            state["intent"] = ConversationIntent.REPORT_FOR_OTHERS.value
            conversation.primary_intent = ConversationIntent.REPORT_FOR_OTHERS
            state["flow"] = "AWAIT_CONSENT"
            result.replies.append(_reply("CONSENT_V1"))
        elif lower.startswith("3") or "ayudar" in lower or "donar" in lower:
            conversation.primary_intent = ConversationIntent.OFFER_HELP
            state["flow"] = "AWAIT_OFFER_TYPE"
            result.replies.append(_reply("ASK_OFFER_TYPE_V1"))
        elif lower.startswith("4") or "consultar" in lower:
            conversation.primary_intent = ConversationIntent.TRACK_CASE
            state["flow"] = "AWAIT_CASE_CODE"
            result.replies.append(_reply("ASK_CASE_CODE_V1"))
        else:
            result.replies.append(_reply("WELCOME_V1"))

    elif flow == "AWAIT_CONSENT":
        if lower.startswith("1") or "acepto" in lower or lower == "si" or lower == "sí":
            _grant_consents(session, person_id, conversation.incident_id, message.id)
            intent = ConversationIntent(state.get("intent", "NEED_HELP"))
            case, report = _create_case_and_report(session, conversation, person_id, intent)
            state.update(
                {
                    "flow": "COLLECTING_NARRATIVE",
                    "case_id": str(case.id),
                    "report_id": str(report.id),
                    "case_code": case.case_code,
                }
            )
            result.replies.append(_reply("ASK_NARRATIVE_V1", code=case.case_code))
        elif lower.startswith("2") or "no acepto" in lower:
            session.add(
                Consent(
                    person_id=person_id,
                    incident_id=conversation.incident_id,
                    purpose=ConsentPurpose.CASE_MANAGEMENT,
                    notice_version=NOTICE_VERSION,
                    status=ConsentStatus.DENIED,
                    captured_via=ChannelType.WHATSAPP,
                    proof_message_id=message.id,
                )
            )
            state["flow"] = "NEW"
            result.replies.append(_reply("CONSENT_DENIED_V1"))
        else:
            result.replies.append(_reply("CONSENT_V1"))

    elif flow == "COLLECTING_NARRATIVE":
        report = session.get(Report, state["report_id"])
        if is_audio:
            state["has_audio"] = True
            state["flow"] = "COLLECTING_LOCATION"
            result.replies.append(_reply("ACK_AUDIO_V1"))
        elif text:
            if report is not None:
                existing = report.narrative or ""
                report.narrative = (existing + "\n" + text).strip()
            state["narrative_saved"] = True
            state["flow"] = "COLLECTING_LOCATION"
            result.replies.append(_reply("ACK_NARRATIVE_V1"))
        else:
            # foto/otros: aceptar sin bloquear (media diferida §4.3)
            state["flow"] = "COLLECTING_LOCATION"
            result.replies.append(_reply("ACK_NARRATIVE_V1"))

    elif flow == "COLLECTING_LOCATION":
        if lower.startswith("3") or "despu" in lower:
            result.replies.append(_submit_report(session, state, conversation))
        else:
            muni = None if is_audio or is_location else _find_municipality(session, text)
            if is_location:
                state["flow"] = "COLLECTING_HOUSEHOLD"
                result.replies.append(_reply("ASK_HOUSEHOLD_V1"))
            elif muni:
                loc = Location(
                    admin1=muni.department_name,
                    admin2=muni.municipality_name,
                    source=LocationSource.GEOCODED_TEXT,
                )
                session.add(loc)
                session.flush()
                report = session.get(Report, state["report_id"])
                if report is not None:
                    report.location_id = loc.id
                state["municipality"] = muni.municipality_name.title()
                state["flow"] = "COLLECTING_HOUSEHOLD"
                result.replies.append(_reply("ASK_HOUSEHOLD_V1"))
            else:
                result.replies.append(_reply("ASK_LOCATION_RETRY_V1"))

    elif flow == "COLLECTING_HOUSEHOLD":
        if lower.startswith("3") and len(lower) <= 2:
            result.replies.append(_submit_report(session, state, conversation))
        else:
            m = re.search(r"\d{1,3}", text)
            if m:
                size = int(m.group(0))
                case = session.get(Case, state["case_id"])
                household = Household(
                    incident_id=conversation.incident_id,
                    reference_code=f"H-{state['case_code']}",
                    member_count=size,
                )
                session.add(household)
                session.flush()
                if case is not None:
                    case.household_id = household.id
                state["household_size"] = size
                state["flow"] = "AWAIT_SUBMIT"
                result.replies.append(
                    _reply(
                        "SUMMARY_V1",
                        code=state.get("case_code", ""),
                        summary=_summary_text(session, state),
                    )
                )
            else:
                result.replies.append(_reply("ASK_HOUSEHOLD_V1"))

    elif flow == "AWAIT_SUBMIT":
        if lower.startswith("1"):
            result.replies.append(_submit_report(session, state, conversation))
        elif lower.startswith("2"):
            state["flow"] = "COLLECTING_NARRATIVE"
            result.replies.append(_reply("ASK_CORRECTION_V1"))
        elif lower.startswith("3"):
            result.replies.append(_submit_report(session, state, conversation))
        else:
            result.replies.append(
                _reply(
                    "SUMMARY_V1",
                    code=state.get("case_code", ""),
                    summary=_summary_text(session, state),
                )
            )

    elif flow == "AWAIT_OFFER_TYPE":
        type_map = {
            "1": ResourceType.MONEY,
            "2": ResourceType.IN_KIND,
            "3": ResourceType.SERVICE,
            "4": ResourceType.TRANSPORT,
            "5": ResourceType.VOLUNTEERING,
        }
        rtype = type_map.get(lower[:1])
        if rtype:
            offer = ResourceOffer(
                incident_id=conversation.incident_id,
                donor_person_id=person_id,
                type=rtype,
                status=OfferStatus.DRAFT,
            )
            session.add(offer)
            session.flush()
            state["offer_id"] = str(offer.id)
            state["flow"] = "COLLECTING_OFFER"
            publish(
                session, event_type="offer.draft_created", aggregate_type="resource_offer",
                aggregate_id=offer.id,
            )
            result.replies.append(_reply("ASK_OFFER_DETAIL_V1"))
        else:
            result.replies.append(_reply("ASK_OFFER_TYPE_V1"))

    elif flow == "COLLECTING_OFFER":
        offer = session.get(ResourceOffer, state["offer_id"]) if state.get("offer_id") else None
        if offer is not None and (text or is_audio):
            if text:
                offer.notes_enc = encrypt_json({"free_text": text})
            offer.status = OfferStatus.PENDING_CONFIRMATION
            code = f"OF-{str(offer.id)[-6:].upper()}"
            state["flow"] = "IDLE_SUBMITTED"
            publish(
                session, event_type="offer.described", aggregate_type="resource_offer",
                aggregate_id=offer.id,
            )
            result.replies.append(_reply("OFFER_THANKS_V1", code=code))
        else:
            result.replies.append(_reply("ASK_OFFER_DETAIL_V1"))

    elif flow == "AWAIT_CASE_CODE":
        result.replies.append(_reply("CASE_NOT_FOUND_V1"))

    else:
        result.replies.append(_reply("FALLBACK_V1"))
        state["flow"] = "NEW"

    state["last_prompt"] = result.replies[-1].prompt_code if result.replies else None
    _save_state(conversation, state)
    return result
