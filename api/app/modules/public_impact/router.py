"""API pública (§14.2 /public/v1/*).

Estas rutas son el ÚNICO acceso del plano público y leen exclusivamente
las proyecciones aprobadas (public_case_profiles, public_story_items,
public_impact_snapshots). Nunca tocan cases, persons, reports ni
locations: PRIV-01 se cumple por construcción, no por filtrado.
"""

import base64
import binascii
import json
import logging
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import log_ctx
from app.core.security import normalize_phone
from app.modules.public_impact.help_service import (
    HelpOfferRejected,
    case_help_options,
    create_help_offer,
)
from app.modules.public_impact.models import (
    ContentReport,
    ContentReportStatus,
    PublicationStatus,
    PublicCaseProfile,
    PublicImpactSnapshot,
    PublicStoryItem,
)

logger = logging.getLogger("public_api")

router = APIRouter(prefix="/public/v1", tags=["public"])

MAX_PAGE = 50
CACHE_HEADER = "public, max-age=60, stale-while-revalidate=300"


def _encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()


def _decode_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(cursor + pad).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Cursor inválido") from exc


def _profile_public_dict(profile: PublicCaseProfile, items: list[PublicStoryItem]) -> dict:
    return {
        "slug": profile.slug,
        "title": profile.display_title,
        "summary": profile.story_summary,
        "location": profile.coarse_location,
        "household_size_band": profile.household_size_band,
        "progress_percent": float(profile.progress_percent or 0),
        "published_at": profile.published_at,
        "updates": [
            {
                "type": item.type.value,
                "title": item.title,
                "body": item.body,
                "occurred_on": item.occurred_on,
                "cta": item.cta_type.value if item.cta_type else None,
            }
            for item in items
        ],
    }


@router.get("/feed")
def get_feed(
    response: Response,
    admin1: str | None = Query(default=None, description="Departamento (filtro seguro)"),
    admin2: str | None = Query(default=None, description="Municipio (filtro seguro)"),
    order: str = Query(default="recent", pattern="^(recent|gap)$"),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE),
    cursor: str | None = None,
    db: Session = Depends(get_db),
):
    """Impact Feed: solo perfiles PUBLISHED, filtros por geografía gruesa."""
    stmt = sa.select(PublicCaseProfile).where(
        PublicCaseProfile.publication_status == PublicationStatus.PUBLISHED
    )
    if admin1:
        stmt = stmt.where(PublicCaseProfile.coarse_location["admin1"].astext.ilike(admin1))
    if admin2:
        stmt = stmt.where(PublicCaseProfile.coarse_location["admin2"].astext.ilike(admin2))

    if order == "gap":
        # "mayor brecha": menos progreso primero
        stmt = stmt.order_by(
            PublicCaseProfile.progress_percent.asc(), PublicCaseProfile.slug.asc()
        )
    else:
        stmt = stmt.order_by(
            PublicCaseProfile.published_at.desc().nulls_last(), PublicCaseProfile.slug.asc()
        )
        after = _decode_cursor(cursor)
        if after:
            stmt = stmt.where(PublicCaseProfile.slug > after)

    profiles = db.execute(stmt.limit(limit + 1)).scalars().all()
    has_more = len(profiles) > limit
    profiles = profiles[:limit]

    items_by_profile: dict = {}
    if profiles:
        rows = (
            db.execute(
                sa.select(PublicStoryItem)
                .where(
                    PublicStoryItem.profile_id.in_([p.id for p in profiles]),
                    PublicStoryItem.status == PublicationStatus.PUBLISHED,
                )
                .order_by(PublicStoryItem.sort_key.desc())
            )
            .scalars()
            .all()
        )
        for row in rows:
            items_by_profile.setdefault(row.profile_id, []).append(row)

    response.headers["Cache-Control"] = CACHE_HEADER
    return {
        "items": [
            _profile_public_dict(p, items_by_profile.get(p.id, [])) for p in profiles
        ],
        "next_cursor": _encode_cursor(profiles[-1].slug) if has_more and profiles else None,
    }


@router.get("/cases/{slug}")
def get_public_case(slug: str, response: Response, db: Session = Depends(get_db)):
    profile = db.execute(
        sa.select(PublicCaseProfile).where(
            PublicCaseProfile.slug == slug,
            PublicCaseProfile.publication_status == PublicationStatus.PUBLISHED,
        )
    ).scalar_one_or_none()
    if profile is None:
        # 404 idéntico para "no existe" y "no publicado" (no filtra existencia)
        raise HTTPException(status_code=404, detail="No encontrado")
    items = (
        db.execute(
            sa.select(PublicStoryItem)
            .where(
                PublicStoryItem.profile_id == profile.id,
                PublicStoryItem.status == PublicationStatus.PUBLISHED,
            )
            .order_by(PublicStoryItem.sort_key.desc())
        )
        .scalars()
        .all()
    )
    response.headers["Cache-Control"] = CACHE_HEADER
    return _profile_public_dict(profile, list(items))


@router.get("/impact")
def get_impact(
    response: Response,
    geography_level: str = Query(default="NATIONAL"),
    db: Session = Depends(get_db),
):
    """Transparency Dashboard: snapshots agregados con supresión aplicada."""
    latest_as_of = db.execute(
        sa.select(sa.func.max(PublicImpactSnapshot.as_of)).where(
            PublicImpactSnapshot.geography_level == geography_level
        )
    ).scalar()
    if latest_as_of is None:
        response.headers["Cache-Control"] = CACHE_HEADER
        return {"as_of": None, "metrics": {}, "by_municipality": [], "definitions": {}}

    rows = (
        db.execute(
            sa.select(PublicImpactSnapshot).where(
                PublicImpactSnapshot.as_of == latest_as_of,
            )
        )
        .scalars()
        .all()
    )

    metrics: dict[str, dict] = {}
    categories: list[dict] = []
    municipalities: list[dict] = []
    for row in rows:
        value = float(row.value_numeric)
        if row.geography_level == "MUNICIPALITY":
            municipalities.append(
                {
                    "municipality": row.geography_code,
                    "metric": row.metric_code,
                    "value": value,
                    "suppressed": row.suppression_applied,
                }
            )
        elif row.metric_code.startswith("need_category::"):
            categories.append(
                {
                    "catalog_code": row.metric_code.split("::", 1)[1],
                    "value": value,
                    "suppressed": row.suppression_applied,
                }
            )
        else:
            metrics[row.metric_code] = {
                "value": value,
                "suppressed": row.suppression_applied,
            }

    response.headers["Cache-Control"] = CACHE_HEADER
    return {
        "as_of": latest_as_of,
        "privacy_threshold": rows[0].privacy_threshold if rows else None,
        "metrics": metrics,
        "top_categories": sorted(categories, key=lambda c: -c["value"]),
        "by_municipality": sorted(municipalities, key=lambda m: -m["value"]),
        "definitions": {
            "cases_received": "Casos registrados por cualquier canal.",
            "cases_verified": "Casos con validación PASS registrada.",
            "cases_served": "Casos con al menos una entrega verificada.",
            "needs_coverage_pct_*": "Cantidad cubierta / cantidad confirmada, por horizonte.",
            "suppressed": "Celda con menos observaciones que el umbral de privacidad.",
        },
    }


@router.post("/content-reports", status_code=201)
async def report_content(request: Request, db: Session = Depends(get_db)):
    """Reporte de contenido del feed (§6.3). Sin autenticación, sin PII en logs."""
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    slug = (body.get("slug") or "").strip()
    reason = (body.get("reason_code") or "OTHER").strip()[:64]
    details = (body.get("details") or "").strip()[:2000] or None

    profile = db.execute(
        sa.select(PublicCaseProfile).where(PublicCaseProfile.slug == slug)
    ).scalar_one_or_none()
    report = ContentReport(
        profile_id=profile.id if profile else None,
        reason_code=reason,
        details_redacted=details,
        status=ContentReportStatus.RECEIVED,
    )
    db.add(report)
    db.commit()
    log_ctx(logger, logging.INFO, "content report received", reason=reason)
    return {"status": "received"}


# ── Ofrecer ayuda desde la web ─────────────────────────────────────────


@router.get("/cases/{slug}/help-options")
def get_help_options(slug: str, response: Response, db: Session = Depends(get_db)):
    """Qué le falta a un caso publicado, para llenar el formulario de ayuda.

    Devuelve categoría, unidad y cantidad pendiente. Nada del hogar: son las
    mismas categorías que ya describe la historia pública, con el número.
    """
    response.headers["Cache-Control"] = CACHE_HEADER
    return {"options": case_help_options(db, slug)}


class HelpOfferIn(BaseModel):
    """Formulario público de ofrecimiento.

    Los límites de longitud son la primera defensa: este endpoint no pide
    autenticación, así que no debe aceptar texto ilimitado.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str | None = Field(default=None, max_length=200)
    offer_type: str = Field(max_length=20)
    contact_name: str = Field(min_length=2, max_length=120)
    contact_phone: str = Field(min_length=7, max_length=20)
    contact_email: EmailStr | None = None
    message: str | None = Field(default=None, max_length=1000)
    catalog_code: str | None = Field(default=None, max_length=64)
    quantity: Decimal | None = Field(default=None, gt=0, le=Decimal("1000000"))
    amount_cop: Decimal | None = Field(default=None, gt=0, le=Decimal("10000000000"))
    consent_contact: bool = False


@router.post("/help-offers", status_code=201)
def submit_help_offer(body: HelpOfferIn, db: Session = Depends(get_db)):
    """Registra un ofrecimiento de ayuda. Sin autenticación, con consentimiento.

    La oferta queda PENDING_CONFIRMATION: nadie del equipo la da por
    disponible hasta hablar con quien ofrece.
    """
    phone = normalize_phone(body.contact_phone)
    if len(phone) < 10:
        raise HTTPException(status_code=422, detail="Teléfono no válido")
    try:
        result = create_help_offer(
            db,
            slug=body.slug,
            offer_type=body.offer_type,
            contact_name=body.contact_name.strip(),
            contact_phone=phone,
            contact_email=body.contact_email,
            message=body.message,
            catalog_code=body.catalog_code,
            quantity=body.quantity,
            amount_cop=body.amount_cop,
            consent_contact=body.consent_contact,
        )
        db.commit()
    except HelpOfferRejected as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result
