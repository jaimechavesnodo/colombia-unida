"""Convenciones comunes de modelos (§7.1 del alcance).

- UUIDv7 generado en aplicación (`default=new_id`).
- timestamptz UTC (`DateTime(timezone=True)`).
- Cantidades `Numeric`, nunca float.
- `version bigint` para optimistic locking en agregados mutables.
- Columnas cifradas: sufijo `_enc`, tipo LargeBinary, helpers en
  `app.core.security`. Columnas HMAC: sufijo `_hmac`, LargeBinary.
- Enums nativos de PostgreSQL con nombre explícito `ck_<enum>`.
"""

import enum
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import declarative_mixin, mapped_column

from app.core.ids import new_id


def utcnow() -> datetime:
    return datetime.now(UTC)


def PgEnum(e: type[enum.Enum], name: str) -> sa.Enum:  # noqa: N802
    """Enum nativo con valores estables (usa .value, no .name)."""
    return sa.Enum(
        e,
        name=name,
        values_callable=lambda x: [i.value for i in x],
    )


@declarative_mixin
class IdMixin:
    id = mapped_column(sa.Uuid, primary_key=True, default=new_id)


@declarative_mixin
class TimestampMixin:
    created_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


@declarative_mixin
class VersionMixin:
    """Optimistic locking (§14.1: ETag/If-Match, 412 ante versión obsoleta)."""

    version = mapped_column(sa.BigInteger, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}  # type: ignore[dict-item]
