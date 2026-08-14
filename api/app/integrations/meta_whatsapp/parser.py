"""Normalización del payload de webhooks de Meta WhatsApp Cloud API.

Convierte el envelope entry[].changes[].value en dataclasses planas para
el resto del sistema. Tolerante por diseño: nunca lanza por campos
faltantes o formas inesperadas; lo no reconocido cae en unknown_events
(§15.1 whatsapp.unknown).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Tipos de mensaje que sabemos normalizar; el resto queda como "unknown".
_MEDIA_TYPES = {"audio", "image", "video", "document"}
_KNOWN_TYPES = _MEDIA_TYPES | {"text", "location", "interactive", "button"}


@dataclass
class InboundMessage:
    wa_id: str
    profile_name: str | None
    provider_message_id: str
    # "text"|"audio"|"image"|"location"|"video"|"document"|"interactive"|"button"|"unknown"
    message_type: str
    text: str | None
    media_id: str | None
    media_mime_type: str | None
    latitude: float | None
    longitude: float | None
    timestamp: datetime
    reply_to_provider_message_id: str | None
    raw: dict


@dataclass
class StatusUpdate:
    provider_message_id: str
    status: str  # "sent"|"delivered"|"read"|"failed"
    timestamp: datetime
    recipient_wa_id: str
    error_code: str | None


@dataclass
class ParsedWebhook:
    phone_number_id: str | None = None
    messages: list[InboundMessage] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)
    unknown_events: list[dict] = field(default_factory=list)


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _parse_timestamp(value: object) -> datetime:
    """Epoch en segundos (string en Cloud API) → datetime UTC; fallback: ahora."""
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(UTC)


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _opt_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extract_text(message_type: str, message: dict) -> str | None:
    """Texto normalizado según el tipo de mensaje."""
    if message_type == "text":
        return _opt_str(_as_dict(message.get("text")).get("body"))
    if message_type == "interactive":
        interactive = _as_dict(message.get("interactive"))
        reply = _as_dict(interactive.get("button_reply")) or _as_dict(
            interactive.get("list_reply")
        )
        # El id es lo que dispara la lógica del bot; el título es el fallback humano.
        return _opt_str(reply.get("id")) or _opt_str(reply.get("title"))
    if message_type == "button":
        button = _as_dict(message.get("button"))
        return _opt_str(button.get("payload")) or _opt_str(button.get("text"))
    if message_type in _MEDIA_TYPES:
        return _opt_str(_as_dict(message.get(message_type)).get("caption"))
    return None


def _parse_message(message: dict, contact_names: dict[str, str]) -> InboundMessage:
    raw_type = message.get("type")
    message_type = raw_type if raw_type in _KNOWN_TYPES else "unknown"

    media_id: str | None = None
    media_mime_type: str | None = None
    if message_type in _MEDIA_TYPES:
        media = _as_dict(message.get(message_type))
        media_id = _opt_str(media.get("id"))
        media_mime_type = _opt_str(media.get("mime_type"))

    latitude: float | None = None
    longitude: float | None = None
    if message_type == "location":
        location = _as_dict(message.get("location"))
        latitude = _opt_float(location.get("latitude"))
        longitude = _opt_float(location.get("longitude"))

    wa_id = _opt_str(message.get("from")) or ""
    return InboundMessage(
        wa_id=wa_id,
        profile_name=contact_names.get(wa_id),
        provider_message_id=_opt_str(message.get("id")) or "",
        message_type=message_type,
        text=_extract_text(message_type, message),
        media_id=media_id,
        media_mime_type=media_mime_type,
        latitude=latitude,
        longitude=longitude,
        timestamp=_parse_timestamp(message.get("timestamp")),
        reply_to_provider_message_id=_opt_str(_as_dict(message.get("context")).get("id")),
        raw=message,
    )


def _parse_status(status: dict) -> StatusUpdate:
    errors = _as_list(status.get("errors"))
    error_code: str | None = None
    if errors:
        code = _as_dict(errors[0]).get("code")
        if code is not None:
            error_code = str(code)
    return StatusUpdate(
        provider_message_id=_opt_str(status.get("id")) or "",
        status=_opt_str(status.get("status")) or "unknown",
        timestamp=_parse_timestamp(status.get("timestamp")),
        recipient_wa_id=_opt_str(status.get("recipient_id")) or "",
        error_code=error_code,
    )


def parse_webhook(payload: dict) -> ParsedWebhook:
    """Normaliza el envelope completo del webhook. Nunca lanza."""
    result = ParsedWebhook()
    if not isinstance(payload, dict):
        return result

    for entry in _as_list(payload.get("entry")):
        for change in _as_list(_as_dict(entry).get("changes")):
            change = _as_dict(change)
            if not change:
                continue
            if change.get("field") != "messages":
                # Evento de otro tipo (templates, calidad del número, etc.)
                result.unknown_events.append(change)
                continue

            value = _as_dict(change.get("value"))
            metadata = _as_dict(value.get("metadata"))
            if result.phone_number_id is None:
                result.phone_number_id = _opt_str(metadata.get("phone_number_id"))

            contact_names: dict[str, str] = {}
            for contact in _as_list(value.get("contacts")):
                contact = _as_dict(contact)
                contact_wa_id = _opt_str(contact.get("wa_id"))
                name = _opt_str(_as_dict(contact.get("profile")).get("name"))
                if contact_wa_id and name:
                    contact_names[contact_wa_id] = name

            for message in _as_list(value.get("messages")):
                message = _as_dict(message)
                if not message:
                    continue
                try:
                    result.messages.append(_parse_message(message, contact_names))
                except Exception:
                    result.unknown_events.append(message)

            for status in _as_list(value.get("statuses")):
                status = _as_dict(status)
                if not status:
                    continue
                try:
                    result.statuses.append(_parse_status(status))
                except Exception:
                    result.unknown_events.append(status)

            if not value.get("messages") and not value.get("statuses"):
                # value tipo "messages" pero sin contenido reconocible
                result.unknown_events.append(change)

    return result
