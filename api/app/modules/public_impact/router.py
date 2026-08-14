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

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import log_ctx
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
