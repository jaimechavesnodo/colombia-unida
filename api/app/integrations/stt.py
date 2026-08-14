"""Adaptador de speech-to-text (STT) para audios de WhatsApp (§9.1).

Proveedor configurable por `settings.stt_provider`:
- "openai": Whisper vía API HTTP (multipart). Sin SDK: httpx directo.
- "disabled" (o sin api key): lanza SttUnavailable y el llamador hace
  fallback a captura manual (criterio AI-02).

Prohibido loggear el audio o la transcripción (§18.1); solo metadatos.
"""

import logging
import math
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.logging import log_ctx

logger = logging.getLogger("integrations.stt")

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_STT_MODEL = "whisper-1"
TIMEOUT_SECONDS = 120.0

# Nombre de archivo requerido por el endpoint multipart, según mime.
_MIME_FILENAMES = {
    "audio/ogg": "audio.ogg",
    "audio/opus": "audio.ogg",
    "audio/mpeg": "audio.mp3",
    "audio/mp3": "audio.mp3",
    "audio/mp4": "audio.mp4",
    "audio/aac": "audio.aac",
    "audio/amr": "audio.amr",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
    "audio/webm": "audio.webm",
    "audio/flac": "audio.flac",
    "audio/x-m4a": "audio.m4a",
}


class SttError(Exception):
    """Fallo del proveedor de transcripción."""


class SttUnavailable(SttError):
    """STT deshabilitado o sin credenciales; procede captura manual."""


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    confidence: float | None
    provider: str
    model: str


def _filename_for_mime(mime_type: str) -> str:
    base = (mime_type or "").split(";")[0].strip().lower()
    if base in _MIME_FILENAMES:
        return _MIME_FILENAMES[base]
    subtype = base.split("/")[-1] or "bin"
    return f"audio.{subtype}"


def _confidence_from_segments(payload: dict) -> float | None:
    """Whisper no da confianza global; se aproxima con exp(avg_logprob)."""
    segments = payload.get("segments") or []
    logprobs = [
        s["avg_logprob"]
        for s in segments
        if isinstance(s, dict) and isinstance(s.get("avg_logprob"), (int, float))
    ]
    if not logprobs:
        return None
    avg = sum(logprobs) / len(logprobs)
    return max(0.0, min(1.0, math.exp(avg)))


def _transcribe_openai(audio_bytes: bytes, mime_type: str, api_key: str) -> TranscriptionResult:
    filename = _filename_for_mime(mime_type)
    try:
        response = httpx.post(
            OPENAI_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": OPENAI_STT_MODEL,
                "language": "es",
                "response_format": "verbose_json",
            },
            files={"file": (filename, audio_bytes, mime_type or "application/octet-stream")},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise SttError(f"stt request failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise SttError(f"stt provider returned {response.status_code}")
    try:
        payload = response.json()
        text = payload["text"]
    except (ValueError, KeyError) as exc:
        raise SttError("stt provider returned an invalid payload") from exc
    return TranscriptionResult(
        text=text,
        language=payload.get("language"),
        confidence=_confidence_from_segments(payload),
        provider="openai",
        model=OPENAI_STT_MODEL,
    )


def transcribe(audio_bytes: bytes, mime_type: str) -> TranscriptionResult:
    """Transcribe un audio según el proveedor configurado.

    Lanza SttUnavailable si el STT está deshabilitado o sin credenciales;
    SttError ante fallos del proveedor. Nunca loggea contenido.
    """
    settings = get_settings()
    provider = (settings.stt_provider or "disabled").lower()
    if provider == "disabled":
        raise SttUnavailable("stt provider disabled")
    if provider == "openai":
        if not settings.openai_api_key:
            raise SttUnavailable("openai api key not configured")
        started = time.monotonic()
        try:
            result = _transcribe_openai(audio_bytes, mime_type, settings.openai_api_key)
        except SttError:
            log_ctx(
                logger, logging.WARNING, "stt failed",
                provider="openai", model=OPENAI_STT_MODEL,
                latency_ms=int((time.monotonic() - started) * 1000), status="FAILED",
            )
            raise
        log_ctx(
            logger, logging.INFO, "stt succeeded",
            provider="openai", model=OPENAI_STT_MODEL,
            latency_ms=int((time.monotonic() - started) * 1000), status="SUCCEEDED",
        )
        return result
    raise SttUnavailable(f"unknown stt provider: {provider}")
