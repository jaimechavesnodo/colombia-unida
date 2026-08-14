"""Hash de contraseñas con scrypt (biblioteca estándar, sin dependencias).

scrypt es memory-hard: encarece el ataque por diccionario mucho más que
un SHA con sal. Formato almacenado: scrypt$n$r$p$salt_b64$hash_b64, así
que los parámetros viajan con el hash y se pueden endurecer sin romper
las credenciales existentes.
"""

import base64
import hashlib
import hmac
import os

N = 2**15  # coste CPU/memoria (~32 MB por verificación)
R = 8
P = 1
SALT_BYTES = 16
KEY_LEN = 32


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    key = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=KEY_LEN)
    return "$".join(
        [
            "scrypt",
            str(N),
            str(R),
            str(P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(key_b64)),
        )
        return hmac.compare_digest(key, base64.b64decode(key_b64))
    except (ValueError, TypeError):
        return False
