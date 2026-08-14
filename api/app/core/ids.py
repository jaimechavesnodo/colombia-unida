"""Generación de identificadores.

UUIDv7 generado en aplicación (§7.1): claves distribuidas con orden
temporal aproximado. Los códigos humanos (p. ej. de caso) son separados.
"""

import secrets
import uuid

from uuid6 import uuid7

# Alfabeto sin ambigüedades (sin 0/O, 1/I/L) para códigos dictables por voz.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def new_id() -> uuid.UUID:
    return uuid7()


def new_short_code(prefix: str, length: int = 6) -> str:
    """Código corto humano, ej. CU-7K2M9P, para citar un caso por WhatsApp."""
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
    return f"{prefix}-{body}"
