"""Logging estructurado JSON.

Regla del alcance (§18.1): los logs no contienen cuerpos de mensajes,
prompts, teléfonos, tokens ni URLs firmadas. Solo códigos e IDs opacos.
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Claves que jamás deben aparecer en logs, ni siquiera por accidente.
FORBIDDEN_KEYS = {
    "phone", "phone_number", "wa_id", "token", "access_token", "authorization",
    "body", "text", "narrative", "prompt", "signed_url", "national_id", "email",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update({k: v for k, v in extra.items() if k not in FORBIDDEN_KEYS})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn access logs traen paths con posibles query params; los silenciamos
    logging.getLogger("uvicorn.access").setLevel("WARNING")


def log_ctx(logger: logging.Logger, level: int, msg: str, **ctx) -> None:
    """Log con contexto estructurado filtrado de PII."""
    logger.log(level, msg, extra={"ctx": ctx})
