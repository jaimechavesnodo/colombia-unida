"""Criptografía de aplicación.

- AES-256-GCM para campos [PRIV]/[SENS] (cifrado de aplicación, §13.2).
- HMAC-SHA256 para índices de igualdad/dedupe sin exponer el valor
  (teléfonos, emails, documentos) — no reversible.

Las claves llegan por entorno en base64 (32 bytes). En producción viven
solo en las variables de EasyPanel.
"""

import base64
import hashlib
import hmac as hmac_mod
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_NONCE_LEN = 12


def _key(raw_b64: str, purpose: str) -> bytes:
    if not raw_b64:
        raise RuntimeError(f"Clave {purpose} no configurada")
    key = base64.b64decode(raw_b64)
    if len(key) != 32:
        raise RuntimeError(f"Clave {purpose} debe ser de 32 bytes")
    return key


def generate_key_b64() -> str:
    """Utilidad para generar claves nuevas (setup)."""
    return base64.b64encode(os.urandom(32)).decode()


def encrypt_bytes(plaintext: bytes) -> bytes:
    key = _key(get_settings().app_encryption_key, "APP_ENCRYPTION_KEY")
    nonce = os.urandom(_NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt_bytes(ciphertext: bytes) -> bytes:
    key = _key(get_settings().app_encryption_key, "APP_ENCRYPTION_KEY")
    nonce, body = ciphertext[:_NONCE_LEN], ciphertext[_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, body, None)


def encrypt_text(value: str) -> bytes:
    return encrypt_bytes(value.encode("utf-8"))


def decrypt_text(ciphertext: bytes) -> str:
    return decrypt_bytes(ciphertext).decode("utf-8")


def encrypt_json(value: Any) -> bytes:
    return encrypt_bytes(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def decrypt_json(ciphertext: bytes) -> Any:
    return json.loads(decrypt_bytes(ciphertext))


def hmac_index(value: str) -> bytes:
    """HMAC determinístico para búsqueda por igualdad (valor normalizado)."""
    key = _key(get_settings().app_hmac_key, "APP_HMAC_KEY")
    return hmac_mod.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def normalize_phone(phone: str) -> str:
    """Normaliza a E.164 sin '+' (formato wa_id de Meta): solo dígitos."""
    digits = "".join(c for c in phone if c.isdigit())
    # Colombia: si llegan 10 dígitos empezando por 3 (celular), anteponer 57
    if len(digits) == 10 and digits.startswith("3"):
        digits = "57" + digits
    return digits


def phone_hmac(phone: str) -> bytes:
    return hmac_index(normalize_phone(phone))
