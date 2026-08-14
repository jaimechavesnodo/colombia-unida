"""Proyecciones públicas y snapshots agregados (§6.3, §6.4, §13.1).

Reglas duras que este módulo hace cumplir:
- El plano público NUNCA lee tablas protegidas: solo escribe/lee
  public_case_profiles, public_story_items y public_impact_snapshots.
- Un caso solo se proyecta si existe consentimiento de PUBLICACIÓN
  vigente y aprobación humana (publication_status APPROVED/PUBLISHED).
- Ubicación gruesa (departamento/municipio), banda de tamaño de hogar,
  nunca nombres, teléfonos, documentos ni coordenadas (PRIV-01).
- Los snapshots aplican supresión de celdas pequeñas (umbral k).
"""

import logging
import re
import unicodedata
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.logging import log_ctx
from app.core.model_base import utcnow
from app.core.outbox import publish
from app.modules.cases.models import (
    Case,
    CaseStatus,
    Need,
    NeedCatalog,
    NeedStatus,
    Report,
    ReportSubject,
)
from app.modules.identity.models import (
    Consent,
    ConsentPurpose,
    ConsentStatus,
    Household,
    Location,
)
from app.modules.public_impact.models import (
    ConsentBasis,
    CtaType,
    ModerationState,
    PublicationStatus,
    PublicCaseProfile,
    PublicImpactSnapshot,
    PublicStoryItem,
    StoryItemType,
)

logger = logging.getLogger("public_impact")

K_THRESHOLD = 5  # umbral de supresión de celdas pequeñas (§6.4)

HOUSEHOLD_BANDS = ((2, "1-2"), (5, "3-5"), (9, "6-9"), (10**6, "10+"))


class PublicationNotAllowed(Exception):
    """Falta consentimiento de publicación o el caso no es publicable."""


def household_band(member_count: int | None) -> str | None:
    if not member_count:
        return None
    for ceiling, label in HOUSEHOLD_BANDS:
        if member_count <= ceiling:
            return label
    return None


def slugify(text: str, suffix: str) -> str:
    base = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()[:48]
    return f"{base or 'caso'}-{suffix.lower()}"


def has_publication_consent(session: Session, case: Case) -> bool:
    """Consentimiento de PUBLICACIÓN vigente de alguna persona del caso."""
    from app.modules.cases.models import CasePerson

    return (
        session.execute(
            sa.select(sa.func.count())
            .select_from(Consent)
            .join(CasePerson, CasePerson.person_id == Consent.person_id)
            .where(
                CasePerson.case_id == case.id,
                Consent.purpose == ConsentPurpose.PUBLICATION,
                Consent.status == ConsentStatus.GRANTED,
            )
        ).scalar()
        or 0
    ) > 0


def case_progress_percent(session: Session, case: Case) -> Decimal:
    """Progreso agregado = cantidad cubierta / solicitada de sus necesidades."""
    row = session.execute(
        sa.select(
            sa.func.coalesce(sa.func.sum(Need.covered_qty), 0),
            sa.func.coalesce(
                sa.func.sum(sa.func.coalesce(Need.confirmed_qty, Need.requested_qty)), 0
            ),
        ).where(
            Need.case_id == case.id,
            Need.status.notin_([NeedStatus.REJECTED, NeedStatus.DUPLICATE, NeedStatus.CANCELLED]),
        )
    ).first()
    covered, requested = (row[0] or 0), (row[1] or 0)
    if not requested:
        return Decimal(0)
    pct = (Decimal(covered) / Decimal(requested)) * 100
    return min(pct, Decimal(100)).quantize(Decimal("1"))


def coarse_location(session: Session, case: Case) -> dict | None:
    """Solo departamento/municipio — nunca el punto exacto (§13.1 SENS)."""
    loc = session.execute(
        sa.select(Location)
        .join(Report, Report.location_id == Location.id)
        .join(ReportSubject, ReportSubject.report_id == Report.id)
        .where(ReportSubject.case_id == case.id)
        .limit(1)
    ).scalar_one_or_none()
    if loc is None:
        return None
    return {"admin1": loc.admin1, "admin2": loc.admin2, "country": loc.country_code}


def build_public_profile(
    session: Session,
    case: Case,
    display_title: str,
    story_summary: str,
    approved_by=None,
    publish_now: bool = False,
) -> PublicCaseProfile:
    """Crea (o actualiza) la proyección pública de un caso aprobado."""
    if case.status in (
        CaseStatus.DRAFT, CaseStatus.INCOMPLETE, CaseStatus.DUPLICATE,
        CaseStatus.REJECTED, CaseStatus.CANCELLED, CaseStatus.SUSPICIOUS,
    ):
        raise PublicationNotAllowed(f"Caso en estado no publicable: {case.status.value}")
    if not has_publication_consent(session, case):
        raise PublicationNotAllowed("Sin consentimiento de publicación vigente")

    profile = session.execute(
        sa.select(PublicCaseProfile).where(PublicCaseProfile.case_id == case.id)
    ).scalar_one_or_none()

    household = session.get(Household, case.household_id) if case.household_id else None
    payload = {
        "display_title": display_title,
        "story_summary": story_summary,
        "coarse_location": coarse_location(session, case),
        "household_size_band": household_band(household.member_count if household else None),
        "progress_percent": case_progress_percent(session, case),
        "consent_basis": ConsentBasis.EXPLICIT_CONSENT,
        "approved_by": approved_by,
    }
    if profile is None:
        profile = PublicCaseProfile(
            case_id=case.id,
            slug=slugify(display_title, case.case_code.split("-")[-1]),
            publication_status=PublicationStatus.APPROVED,
            **payload,
        )
        session.add(profile)
        session.flush()
    else:
        for k, v in payload.items():
            setattr(profile, k, v)

    if publish_now:
        profile.publication_status = PublicationStatus.PUBLISHED
        profile.published_at = utcnow()
        publish(
            session,
            event_type="public_profile.published",
            aggregate_type="public_profile",
            aggregate_id=profile.id,
            payload={"slug": profile.slug},
        )
    return profile


def add_story_item(
    session: Session,
    profile: PublicCaseProfile,
    item_type: StoryItemType,
    title: str,
    body: str | None = None,
    cta: CtaType | None = CtaType.HELP_THIS_CASE,
    sort_key: int = 0,
    published: bool = True,
) -> PublicStoryItem:
    item = PublicStoryItem(
        profile_id=profile.id,
        type=item_type,
        title=title,
        body=body,
        cta_type=cta,
        cta_target=profile.slug if cta == CtaType.HELP_THIS_CASE else None,
        sort_key=sort_key,
        status=PublicationStatus.PUBLISHED if published else PublicationStatus.PENDING_REVIEW,
        moderation_state=ModerationState.APPROVED if published else ModerationState.PENDING,
    )
    session.add(item)
    return item


def withdraw_profile(session: Session, profile: PublicCaseProfile, reason: str) -> None:
    """Revocación (PRIV-02): sale del feed y queda prueba de la revocación."""
    profile.publication_status = PublicationStatus.WITHDRAWN
    session.execute(
        sa.update(PublicStoryItem)
        .where(PublicStoryItem.profile_id == profile.id)
        .values(status=PublicationStatus.WITHDRAWN)
    )
    publish(
        session,
        event_type="public_profile.withdrawn",
        aggregate_type="public_profile",
        aggregate_id=profile.id,
        payload={"reason": reason},
    )
    log_ctx(logger, logging.INFO, "public profile withdrawn", profile_id=str(profile.id))


# ── Snapshots agregados (§6.4) ─────────────────────────────────────────


def _upsert_snapshot(
    session: Session, incident_id, geo_level: str, geo_code: str,
    metric: str, value, as_of, suppressed: bool = False,
) -> None:
    existing = session.execute(
        sa.select(PublicImpactSnapshot).where(
            PublicImpactSnapshot.incident_id == incident_id,
            PublicImpactSnapshot.geography_level == geo_level,
            PublicImpactSnapshot.geography_code == geo_code,
            PublicImpactSnapshot.metric_code == metric,
            PublicImpactSnapshot.dimension_json == "{}",
            PublicImpactSnapshot.as_of == as_of,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.value_numeric = value
        existing.suppression_applied = suppressed
        return
    session.add(
        PublicImpactSnapshot(
            incident_id=incident_id,
            geography_level=geo_level,
            geography_code=geo_code,
            metric_code=metric,
            dimension_json="{}",
            value_numeric=value,
            as_of=as_of,
            privacy_threshold=K_THRESHOLD,
            suppression_applied=suppressed,
        )
    )


def rebuild_impact_snapshots(session: Session, incident_id) -> int:
    """Recalcula los KPIs públicos del incidente (§6.4 KPIs base)."""
    as_of = utcnow().replace(microsecond=0)
    written = 0

    counts = dict(
        session.execute(
            sa.select(Case.status, sa.func.count())
            .where(Case.incident_id == incident_id)
            .group_by(Case.status)
        ).all()
    )
    total_cases = sum(counts.values())
    verified = sum(
        v for k, v in counts.items()
        if k in (
            CaseStatus.VERIFIED, CaseStatus.ACTIVE, CaseStatus.PARTIALLY_SERVED,
            CaseStatus.SERVED, CaseStatus.CLOSED,
        )
    )
    served = sum(
        v for k, v in counts.items()
        if k in (CaseStatus.PARTIALLY_SERVED, CaseStatus.SERVED, CaseStatus.CLOSED)
    )
    for metric, value in (
        ("cases_received", total_cases),
        ("cases_verified", verified),
        ("cases_served", served),
    ):
        _upsert_snapshot(session, incident_id, "NATIONAL", "CO", metric, value, as_of)
        written += 1

    # Necesidades por horizonte y cobertura
    for horizon, cnt, requested, covered in session.execute(
        sa.select(
            Need.horizon,
            sa.func.count(),
            sa.func.coalesce(
                sa.func.sum(sa.func.coalesce(Need.confirmed_qty, Need.requested_qty)), 0
            ),
            sa.func.coalesce(sa.func.sum(Need.covered_qty), 0),
        )
        .join(Case, Case.id == Need.case_id)
        .where(Case.incident_id == incident_id)
        .group_by(Need.horizon)
    ).all():
        _upsert_snapshot(
            session, incident_id, "NATIONAL", "CO",
            f"needs_count_{horizon.value.lower()}", cnt, as_of,
        )
        coverage = (Decimal(covered) / Decimal(requested) * 100) if requested else Decimal(0)
        _upsert_snapshot(
            session, incident_id, "NATIONAL", "CO",
            f"needs_coverage_pct_{horizon.value.lower()}",
            coverage.quantize(Decimal("1")), as_of,
        )
        written += 2

    # Top categorías solicitadas
    for code, cnt in session.execute(
        sa.select(NeedCatalog.code, sa.func.count())
        .join(Need, Need.catalog_id == NeedCatalog.id)
        .join(Case, Case.id == Need.case_id)
        .where(Case.incident_id == incident_id)
        .group_by(NeedCatalog.code)
        .order_by(sa.func.count().desc())
        .limit(8)
    ).all():
        suppressed = cnt < K_THRESHOLD
        _upsert_snapshot(
            session, incident_id, "NATIONAL", "CO", f"need_category::{code}",
            0 if suppressed else cnt, as_of, suppressed=suppressed,
        )
        written += 1

    # Cobertura por municipio (con supresión de celdas pequeñas)
    for admin2, cnt in session.execute(
        sa.select(Location.admin2, sa.func.count(sa.distinct(Case.id)))
        .select_from(Case)
        .join(ReportSubject, ReportSubject.case_id == Case.id)
        .join(Report, Report.id == ReportSubject.report_id)
        .join(Location, Location.id == Report.location_id)
        .where(Case.incident_id == incident_id, Location.admin2.isnot(None))
        .group_by(Location.admin2)
    ).all():
        suppressed = cnt < K_THRESHOLD
        _upsert_snapshot(
            session, incident_id, "MUNICIPALITY", admin2, "cases_received",
            0 if suppressed else cnt, as_of, suppressed=suppressed,
        )
        written += 1

    log_ctx(logger, logging.INFO, "impact snapshots rebuilt", metrics=written)
    return written
