"""Carga de datos semilla idempotente.

Uso: python -m app.seeds
Carga: roles, incidente del terremoto, geografía DIVIPOLA y
need_catalog v1 (borrador). Reejecutable sin duplicar (upsert por claves
naturales).
"""

import json
import logging
from pathlib import Path

import sqlalchemy as sa

from app.core.db import get_session_factory
from app.core.ids import new_id
from app.core.logging import log_ctx, setup_logging
from app.modules.cases.models import NeedCatalog, NeedHorizon
from app.modules.identity.models import (
    GeoDivipola,
    Incident,
    IncidentStatus,
    Role,
    RoleCode,
)
from app.seeds.need_catalog_v1 import CATALOG_VERSION, ITEMS, ROOTS

logger = logging.getLogger("seeds")

DATA_DIR = Path(__file__).parent / "data"

INCIDENT_CODE = "CO-EQ-2026-08"


def seed_roles(session) -> int:
    count = 0
    for code in RoleCode:
        exists = session.execute(
            sa.select(Role.id).where(Role.code == code, Role.version == 1)
        ).first()
        if not exists:
            session.add(Role(code=code, name=code.value.replace("_", " ").title(), version=1))
            count += 1
    return count


def seed_incident(session) -> bool:
    exists = session.execute(
        sa.select(Incident.id).where(Incident.code == INCIDENT_CODE)
    ).first()
    if exists:
        return False
    session.add(
        Incident(
            code=INCIDENT_CODE,
            name="Terremoto Colombia — 10 de agosto de 2026",
            status=IncidentStatus.ACTIVE,
            starts_at=sa.func.to_timestamp(1786665600),  # 2026-08-10 UTC aprox
        )
    )
    return True


def seed_divipola(session) -> int:
    rows = json.loads((DATA_DIR / "divipola.json").read_text())
    count = 0
    for r in rows:
        code = r["cod_mpio"]
        exists = session.get(GeoDivipola, code)
        if exists:
            continue

        def _num(v: str | None):
            return float(v.replace(",", ".")) if v else None

        session.add(
            GeoDivipola(
                municipality_code=code,
                department_code=r["cod_dpto"],
                department_name=r["dpto"],
                municipality_name=r["nom_mpio"],
                municipality_type=r.get("tipo_municipio"),
                longitude=_num(r.get("longitud")),
                latitude=_num(r.get("latitud")),
            )
        )
        count += 1
    return count


def seed_need_catalog(session) -> int:
    count = 0
    root_ids: dict[str, object] = {}
    for code, name in ROOTS:
        row = session.execute(
            sa.select(NeedCatalog).where(
                NeedCatalog.code == code, NeedCatalog.version == CATALOG_VERSION
            )
        ).scalar_one_or_none()
        if row is None:
            row = NeedCatalog(
                id=new_id(),
                code=code,
                name_es=name,
                unit_code="UNIT",
                default_horizon=NeedHorizon.EMERGENCY,
                active=True,
                version=CATALOG_VERSION,
            )
            session.add(row)
            count += 1
        root_ids[code] = row.id

    for code, name, unit, horizon, parent in ITEMS:
        exists = session.execute(
            sa.select(NeedCatalog.id).where(
                NeedCatalog.code == code, NeedCatalog.version == CATALOG_VERSION
            )
        ).first()
        if exists:
            continue
        session.add(
            NeedCatalog(
                code=code,
                name_es=name,
                unit_code=unit,
                default_horizon=NeedHorizon(horizon),
                parent_id=root_ids.get(parent),
                active=True,
                version=CATALOG_VERSION,
            )
        )
        count += 1
    return count


def main() -> None:
    setup_logging()
    session = get_session_factory()()
    try:
        roles = seed_roles(session)
        incident = seed_incident(session)
        divipola = seed_divipola(session)
        catalog = seed_need_catalog(session)
        session.commit()
        log_ctx(
            logger, logging.INFO, "seeds applied",
            roles=roles, incident_created=incident, divipola=divipola, catalog=catalog,
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
