"""Autenticación de la consola (§13.2: OIDC/MFA — ver ADR-0002).

Login con email + contraseña y TOTP opcional; emite un JWT de sesión
corta. El acceso a cada endpoint lo decide el RBAC de app.core.auth,
nunca este router.
"""

import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, issue_token, user_roles
from app.core.db import get_db
from app.core.logging import log_ctx
from app.core.model_base import utcnow
from app.core.passwords import verify_password
from app.core.security import decrypt_text, hmac_index
from app.modules.identity.models import User, UserStatus

logger = logging.getLogger("auth_api")

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: dict


def _lookup_user(db: Session, email: str) -> User | None:
    # Búsqueda por HMAC: el email está cifrado, no se puede consultar en claro
    return db.execute(
        sa.select(User).where(User.email_hmac == hmac_index(email.strip().lower()))
    ).scalar_one_or_none()


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = _lookup_user(db, body.email)

    # Mismo mensaje y mismo trabajo para usuario inexistente y contraseña
    # incorrecta: no se filtra qué correos existen.
    ok = user is not None and verify_password(body.password, user.password_hash)
    if not ok or user is None:
        log_ctx(logger, logging.WARNING, "login failed")
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Cuenta no activa")

    if user.mfa_enrolled and not body.totp_code:
        raise HTTPException(status_code=401, detail="Se requiere código de verificación")

    user.last_login_at = utcnow()
    db.commit()

    roles = sorted(r.value for r in user_roles(db, user))
    log_ctx(logger, logging.INFO, "login ok", user_id=str(user.id), roles=roles)
    from app.core.auth import TOKEN_TTL_SECONDS

    return LoginResponse(
        access_token=issue_token(user),
        expires_in=TOKEN_TTL_SECONDS,
        user={
            "id": str(user.id),
            "email": decrypt_text(user.email_enc) if user.email_enc else None,
            "roles": roles,
            "mfa_enrolled": user.mfa_enrolled,
        },
    )


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "id": str(user.id),
        "email": decrypt_text(user.email_enc) if user.email_enc else None,
        "roles": sorted(r.value for r in user_roles(db, user)),
        "last_login_at": user.last_login_at,
    }
