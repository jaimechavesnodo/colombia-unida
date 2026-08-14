"""Verificación de firma de webhooks de Meta (X-Hub-Signature-256).

Meta firma el cuerpo crudo del request con HMAC-SHA256 usando el app secret.
La comparación usa hmac.compare_digest para evitar timing attacks.
"""

import hashlib
import hmac

_PREFIX = "sha256="


def verify_signature(app_secret: str, body: bytes, signature_header: str | None) -> bool:
    """Valida el header X-Hub-Signature-256 contra el cuerpo crudo.

    Devuelve False ante header ausente, esquema distinto de sha256, hex
    malformado o firma que no coincide. Nunca lanza.
    """
    if not app_secret or not signature_header or not isinstance(body, bytes):
        return False
    try:
        header = signature_header.strip()
        if not header.lower().startswith(_PREFIX):
            return False
        provided_hex = header[len(_PREFIX) :].strip().lower()
        if not provided_hex:
            return False
        expected_hex = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_hex, provided_hex)
    except Exception:
        return False
