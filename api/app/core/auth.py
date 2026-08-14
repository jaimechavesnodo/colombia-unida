"""Autenticación y autorización para /v1 (RBAC, deny by default §13.2).

JWT de sesión corta firmado con JWT_SECRET (HS256, sin dependencia
externa: firma HMAC propia). El login con TOTP se completa en la consola
(M7); este módulo provee emisión/verificación de tokens y dependencias
de FastAPI para proteger routers.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.logging import log_ctx
from app.modules.identity.models import Role, RoleCode, User, UserRoleAssignment, UserStatus

logger = logging.getLogger("auth")

TOKEN_TTL_SECONDS = 8 * 3600  # sesiones cortas (§13.2)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def issue_token(user: User) -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError("JWT_SECRET no configurado")
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": str(user.id),
                "authz_version": user.authz_version,
                "iat": int(time.time()),
                "exp": int(time.time()) + TOKEN_TTL_SECONDS,
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify_token(token: str) -> dict | None:
    secret = get_settings().jwt_secret
    if not secret:
        return None
    try:
        header, payload, sig = token.split(".")
        signing_input = f"{header}.{payload}".encode()
        expected = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_unb64(payload))
        if claims.get("exp", 0) < time.time():
            return None
        return claims
    except Exception:
        return None


def get_current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Credenciales requeridas")
    claims = verify_token(authorization.removeprefix("Bearer ").strip())
    if claims is None:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    user = db.get(User, uuid.UUID(claims["sub"]))
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="Cuenta no activa")
    if user.authz_version != claims.get("authz_version"):
        raise HTTPException(status_code=401, detail="Sesión revocada")
    return user


def user_roles(db: Session, user: User) -> set[RoleCode]:
    rows = db.execute(
        sa.select(Role.code)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
        .where(UserRoleAssignment.user_id == user.id)
    ).all()
    return {r[0] for r in rows}


def require_roles(*allowed: RoleCode):
    """Dependencia: el usuario debe tener alguno de los roles indicados.

    ADMIN no hereda acceso operativo automáticamente (SoD §2.2); inclúyelo
    explícitamente donde corresponda.
    """

    def dependency(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        roles = user_roles(db, user)
        if not roles.intersection(allowed):
            log_ctx(
                logger, logging.WARNING, "authz denied",
                user_id=str(user.id), needed=[r.value for r in allowed],
            )
            # 403 seguro sin filtrar existencia de recursos (SEC-01)
            raise HTTPException(status_code=403, detail="Sin permiso para esta operación")
        return user

    return dependency
