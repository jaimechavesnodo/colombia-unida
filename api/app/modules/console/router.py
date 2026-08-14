"""API de la consola operativa (§6.1: Command Center, casos, necesidades,
recursos, matching, logística, auditoría).

A diferencia de /public/v1, aquí SÍ se leen tablas protegidas — por eso
cada endpoint exige rol (deny by default) y ninguna respuesta incluye
campos [SENS]: ni teléfonos, ni documentos, ni coordenadas exactas, ni
narrativas en claro. El detalle sensible requiere endpoints propios con
access_events, que llegan con M9.
"""

import logging
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.db import get_db
from app.modules.cases.models import (
    Case,
    CasePerson,
    CaseStatus,
    CaseStatusHistory,
    Need,
    NeedCatalog,
    NeedStatus,
    Report,
    ReportSubject,
    Validation,
)
from app.modules.identity.models import Household, Location, RoleCode
from app.modules.intake.models import (
    AgentQueueItem,
    Conversation,
    Message,
    MessageDirection,
    QueueItemStatus,
)
from app.modules.supply.models import (
    Allocation,
    AllocationItem,
    AllocationStatus,
    Match,
    MatchStatus,
    OfferStatus,
    ResourceOffer,
    ResourceOfferItem,
)

logger = logging.getLogger("console_api")

router = APIRouter(prefix="/v1/console", tags=["console"])

# Roles con acceso de lectura a la operación
OPERATIONAL = (
    RoleCode.AGENT,
    RoleCode.SUPERVISOR,
    RoleCode.VALIDATOR,
    RoleCode.ORG_OPERATOR,
    RoleCode.AUDITOR,
    RoleCode.ADMIN,
)

ACTIVE_CASE_STATES = (
    CaseStatus.VERIFIED,
    CaseStatus.ACTIVE,
    CaseStatus.PARTIALLY_SERVED,
)


def _num(v) -> float:
    return float(v) if isinstance(v, Decimal | int | float) else 0.0


def _case_municipality_subquery():
    """Municipio del caso vía su reporte (para listados, sin punto exacto)."""
    return (
        sa.select(Location.admin2)
        .join(Report, Report.location_id == Location.id)
        .join(ReportSubject, ReportSubject.report_id == Report.id)
        .where(ReportSubject.case_id == Case.id)
        .limit(1)
        .correlate(Case)
        .scalar_subquery()
    )


# ── Command Center ─────────────────────────────────────────────────────


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    _user=Depends(require_roles(*OPERATIONAL)),
):
    """Tablero de mando: qué requiere atención ahora (§6.1)."""
    by_status = dict(
        db.execute(sa.select(Case.status, sa.func.count()).group_by(Case.status)).all()
    )
    needs_by_status = dict(
        db.execute(sa.select(Need.status, sa.func.count()).group_by(Need.status)).all()
    )

    qty = db.execute(
        sa.select(
            sa.func.coalesce(
                sa.func.sum(sa.func.coalesce(Need.confirmed_qty, Need.requested_qty)), 0
            ),
            sa.func.coalesce(sa.func.sum(Need.covered_qty), 0),
        ).where(
            Need.status.notin_(
                [NeedStatus.REJECTED, NeedStatus.DUPLICATE, NeedStatus.CANCELLED]
            )
        )
    ).first()
    requested, covered = _num(qty[0]), _num(qty[1])

    queue_rows = db.execute(
        sa.select(AgentQueueItem.queue_code, AgentQueueItem.priority, sa.func.count())
        .where(AgentQueueItem.status.in_([QueueItemStatus.WAITING, QueueItemStatus.ASSIGNED]))
        .group_by(AgentQueueItem.queue_code, AgentQueueItem.priority)
    ).all()

    offers_open = db.execute(
        sa.select(sa.func.count()).select_from(ResourceOffer).where(
            ResourceOffer.status.in_(
                [OfferStatus.AVAILABLE, OfferStatus.PARTIALLY_ALLOCATED]
            )
        )
    ).scalar()

    matches_pending = db.execute(
        sa.select(sa.func.count()).select_from(Match).where(
            Match.status.in_([MatchStatus.PROPOSED, MatchStatus.REVIEW_REQUIRED])
        )
    ).scalar()

    allocations_reserved = db.execute(
        sa.select(sa.func.count()).select_from(Allocation).where(
            Allocation.status == AllocationStatus.RESERVED
        )
    ).scalar()

    unanswered = db.execute(
        sa.select(sa.func.count(sa.distinct(Conversation.id)))
        .select_from(Conversation)
        .join(Message, Message.conversation_id == Conversation.id)
        .where(Message.direction == MessageDirection.INBOUND)
    ).scalar()

    return {
        "cases": {
            "total": sum(by_status.values()),
            "by_status": {k.value: v for k, v in by_status.items()},
            "needs_attention": sum(
                by_status.get(s, 0)
                for s in (CaseStatus.INCOMPLETE, CaseStatus.PENDING_VERIFICATION)
            ),
            "active": sum(by_status.get(s, 0) for s in ACTIVE_CASE_STATES),
        },
        "needs": {
            "total": sum(needs_by_status.values()),
            "by_status": {k.value: v for k, v in needs_by_status.items()},
            "requested_qty": requested,
            "covered_qty": covered,
            "coverage_pct": round(covered / requested * 100, 1) if requested else 0.0,
        },
        "supply": {
            "offers_open": offers_open or 0,
            "matches_pending": matches_pending or 0,
            "allocations_reserved": allocations_reserved or 0,
        },
        "queues": [
            {"queue": q, "priority": p.value, "count": c} for q, p, c in queue_rows
        ],
        "conversations_with_inbound": unanswered or 0,
    }


# ── Casos reportados ───────────────────────────────────────────────────


@router.get("/cases")
def list_cases(
    status: str | None = Query(default=None, description="Filtro por estado"),
    municipality: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _user=Depends(require_roles(*OPERATIONAL)),
):
    """Bandeja de casos. Sin PII: ni nombres, ni teléfonos, ni narrativa."""
    muni = _case_municipality_subquery()
    stmt = (
        sa.select(
            Case.id,
            Case.case_code,
            Case.status,
            Case.priority_band,
            Case.opened_at,
            Case.completeness_score,
            Household.member_count,
            muni.label("municipality"),
            sa.select(sa.func.count())
            .select_from(Need)
            .where(Need.case_id == Case.id)
            .correlate(Case)
            .scalar_subquery()
            .label("needs_count"),
        )
        .join(Household, Household.id == Case.household_id, isouter=True)
        .order_by(Case.opened_at.desc().nulls_last())
        .limit(limit)
    )
    if status:
        try:
            stmt = stmt.where(Case.status == CaseStatus(status))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Estado inválido: {status}") from exc
    if municipality:
        stmt = stmt.where(muni.ilike(f"{municipality}%"))

    rows = db.execute(stmt).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "case_code": r.case_code,
                "status": r.status.value,
                "priority_band": r.priority_band.value if r.priority_band else None,
                "opened_at": r.opened_at,
                "municipality": r.municipality,
                "household_size": r.member_count,
                "needs_count": r.needs_count,
                "completeness": _num(r.completeness_score),
            }
            for r in rows
        ]
    }


@router.get("/cases/{case_id}")
def case_detail(
    case_id: str,
    db: Session = Depends(get_db),
    _user=Depends(require_roles(*OPERATIONAL)),
):
    """Ficha de caso para el panel lateral (§6.2), sin campos sensibles."""
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No encontrado")

    needs = db.execute(
        sa.select(Need, NeedCatalog)
        .join(NeedCatalog, NeedCatalog.id == Need.catalog_id, isouter=True)
        .where(Need.case_id == case.id)
        .order_by(Need.created_at)
    ).all()

    history = db.execute(
        sa.select(CaseStatusHistory)
        .where(CaseStatusHistory.case_id == case.id)
        .order_by(CaseStatusHistory.changed_at)
    ).scalars().all()

    validations = db.execute(
        sa.select(Validation).where(Validation.case_id == case.id)
    ).scalars().all()

    persons = db.execute(
        sa.select(CasePerson.role, sa.func.count())
        .where(CasePerson.case_id == case.id)
        .group_by(CasePerson.role)
    ).all()

    household = db.get(Household, case.household_id) if case.household_id else None
    muni = db.execute(
        sa.select(Location.admin1, Location.admin2)
        .join(Report, Report.location_id == Location.id)
        .join(ReportSubject, ReportSubject.report_id == Report.id)
        .where(ReportSubject.case_id == case.id)
        .limit(1)
    ).first()

    return {
        "id": str(case.id),
        "case_code": case.case_code,
        "status": case.status.value,
        "priority_band": case.priority_band.value if case.priority_band else None,
        "opened_at": case.opened_at,
        "trust_review_state": case.trust_review_state.value
        if case.trust_review_state
        else None,
        # Ubicación gruesa incluso en la consola: el punto exacto es [SENS]
        # y requiere un endpoint con access_event (M9).
        "location": {"admin1": muni.admin1, "admin2": muni.admin2} if muni else None,
        "household": {
            "size": household.member_count if household else None,
            "minors": household.minors_count if household else None,
            "reference_code": household.reference_code if household else None,
        },
        "persons_by_role": {role.value: count for role, count in persons},
        "needs": [
            {
                "id": str(n.id),
                "catalog_code": c.code if c else None,
                "catalog_name": c.name_es if c else None,
                "horizon": n.horizon.value,
                "status": n.status.value,
                "requested_qty": _num(n.requested_qty),
                "confirmed_qty": _num(n.confirmed_qty),
                "covered_qty": _num(n.covered_qty),
                "unit": n.unit_code,
                "urgency": n.urgency.value if n.urgency else None,
                "description": n.description_redacted,
            }
            for n, c in needs
        ],
        "validations": [
            {
                "type": v.type.value,
                "outcome": v.outcome.value,
                "performed_at": v.performed_at,
            }
            for v in validations
        ],
        "history": [
            {
                "from": h.from_status.value if h.from_status else None,
                "to": h.to_status.value,
                "reason_code": h.reason_code,
                "changed_at": h.changed_at,
            }
            for h in history
        ],
    }


# ── Necesidades ────────────────────────────────────────────────────────


@router.get("/needs")
def list_needs(
    status: str | None = None,
    horizon: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    db: Session = Depends(get_db),
    _user=Depends(require_roles(*OPERATIONAL)),
):
    """Cola de necesidades con brecha y antigüedad (aging §6.1)."""
    stmt = (
        sa.select(Need, NeedCatalog, Case.case_code, _case_municipality_subquery())
        .join(NeedCatalog, NeedCatalog.id == Need.catalog_id, isouter=True)
        .join(Case, Case.id == Need.case_id)
        .order_by(Need.created_at)
        .limit(limit)
    )
    if status:
        try:
            stmt = stmt.where(Need.status == NeedStatus(status))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Estado inválido") from exc
    if horizon:
        stmt = stmt.where(Need.horizon == horizon)

    rows = db.execute(stmt).all()
    items = []
    for n, c, case_code, municipality in rows:
        base = _num(n.confirmed_qty) or _num(n.requested_qty)
        covered = _num(n.covered_qty)
        items.append(
            {
                "id": str(n.id),
                "case_code": case_code,
                "municipality": municipality,
                "catalog_code": c.code if c else None,
                "catalog_name": c.name_es if c else None,
                "horizon": n.horizon.value,
                "status": n.status.value,
                "unit": n.unit_code,
                "requested_qty": _num(n.requested_qty),
                "covered_qty": covered,
                "gap_qty": max(base - covered, 0),
                "coverage_pct": round(covered / base * 100, 1) if base else 0.0,
                "created_at": n.created_at,
            }
        )
    return {"items": items}


# ── Inventario de recursos ─────────────────────────────────────────────


@router.get("/resources")
def inventory(
    db: Session = Depends(get_db),
    _user=Depends(require_roles(*OPERATIONAL)),
):
    """Inventario: qué hay ofrecido, reservado y entregado (§6.1 Recursos).

    La identidad del donante no es pública ni se expone aquí por defecto
    (§7.3-32: PRIV); se muestra el tipo de oferta y su disponibilidad.
    """
    rows = db.execute(
        sa.select(ResourceOffer, ResourceOfferItem, NeedCatalog)
        .join(ResourceOfferItem, ResourceOfferItem.offer_id == ResourceOffer.id)
        .join(NeedCatalog, NeedCatalog.id == ResourceOfferItem.catalog_id, isouter=True)
        .order_by(ResourceOffer.created_at.desc())
    ).all()

    items = []
    totals: dict[str, dict] = {}
    for offer, item, cat in rows:
        quantity = _num(item.quantity)
        reserved = _num(item.reserved_qty)
        delivered = _num(item.delivered_qty)
        available = max(quantity - reserved - delivered, 0)
        entry = {
            "offer_id": str(offer.id),
            "offer_type": offer.type.value,
            "offer_status": offer.status.value,
            "item_id": str(item.id),
            "catalog_code": cat.code if cat else None,
            "catalog_name": cat.name_es if cat else item.description_redacted,
            "unit": item.unit_code,
            "quantity": quantity,
            "reserved_qty": reserved,
            "delivered_qty": delivered,
            "available_qty": available,
            "condition": item.condition.value if item.condition else None,
            "available_from": offer.available_from,
            "expiry_date": item.expiry_date,
        }
        items.append(entry)

        key = cat.code if cat else "SIN_CATALOGO"
        agg = totals.setdefault(
            key,
            {
                "catalog_code": key,
                "catalog_name": cat.name_es if cat else "Sin catálogo",
                "unit": item.unit_code,
                "quantity": 0.0,
                "reserved_qty": 0.0,
                "delivered_qty": 0.0,
                "available_qty": 0.0,
                "offers": 0,
            },
        )
        agg["quantity"] += quantity
        agg["reserved_qty"] += reserved
        agg["delivered_qty"] += delivered
        agg["available_qty"] += available
        agg["offers"] += 1

    # Demanda abierta por categoría, para leer inventario contra necesidad
    demand = {}
    for code, gap in db.execute(
        sa.select(
            NeedCatalog.code,
            sa.func.coalesce(
                sa.func.sum(
                    sa.func.coalesce(Need.confirmed_qty, Need.requested_qty)
                    - sa.func.coalesce(Need.covered_qty, 0)
                ),
                0,
            ),
        )
        .join(Need, Need.catalog_id == NeedCatalog.id)
        .where(
            Need.status.in_(
                [
                    NeedStatus.VERIFIED,
                    NeedStatus.OPEN,
                    NeedStatus.PARTIALLY_COVERED,
                    NeedStatus.REPORTED,
                    NeedStatus.PENDING_VERIFICATION,
                ]
            )
        )
        .group_by(NeedCatalog.code)
    ).all():
        demand[code] = _num(gap)

    by_category = []
    for key, agg in totals.items():
        agg["open_demand_qty"] = demand.get(key, 0.0)
        agg["balance_qty"] = agg["available_qty"] - agg["open_demand_qty"]
        by_category.append(agg)
    by_category.sort(key=lambda a: a["balance_qty"])

    return {"items": items, "by_category": by_category}


# ── Cola de matching ───────────────────────────────────────────────────


@router.get("/matching")
def matching_queue(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _user=Depends(require_roles(RoleCode.AGENT, RoleCode.SUPERVISOR, RoleCode.ADMIN)),
):
    """Candidatos por revisar, con los dos puntajes separados (§10.1)."""
    rows = db.execute(
        sa.select(Match, Need, NeedCatalog, Case.case_code, ResourceOfferItem)
        .join(Need, Need.id == Match.need_id)
        .join(Case, Case.id == Need.case_id)
        .join(NeedCatalog, NeedCatalog.id == Need.catalog_id, isouter=True)
        .join(ResourceOfferItem, ResourceOfferItem.id == Match.offer_item_id, isouter=True)
        .where(Match.status.in_([MatchStatus.PROPOSED, MatchStatus.REVIEW_REQUIRED]))
        .order_by(Match.final_rank.desc().nulls_last(), Need.created_at)
        .limit(limit)
    ).all()

    return {
        "items": [
            {
                "match_id": str(m.id),
                "status": m.status.value,
                "case_code": case_code,
                "need_id": str(n.id),
                "catalog_name": c.name_es if c else None,
                "unit": n.unit_code,
                "requested_qty": _num(n.requested_qty),
                "covered_qty": _num(n.covered_qty),
                "offer_available_qty": (
                    max(
                        _num(item.quantity) - _num(item.reserved_qty) - _num(item.delivered_qty),
                        0,
                    )
                    if item
                    else 0.0
                ),
                "humanitarian_priority": _num(m.humanitarian_priority_score),
                "feasibility": _num(m.feasibility_score),
                "final_rank": _num(m.final_rank),
                "explanation": m.explanation_json,
                "algorithm_version": m.algorithm_version,
            }
            for m, n, c, case_code, item in rows
        ]
    }


# ── Asignaciones ───────────────────────────────────────────────────────


@router.get("/allocations")
def list_allocations(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _user=Depends(require_roles(*OPERATIONAL)),
):
    """Asignaciones vigentes con su cumplimiento (§6.1 Logística)."""
    rows = db.execute(
        sa.select(Allocation, AllocationItem, NeedCatalog, Case.case_code)
        .join(AllocationItem, AllocationItem.allocation_id == Allocation.id)
        .join(Need, Need.id == AllocationItem.need_id, isouter=True)
        .join(Case, Case.id == Need.case_id, isouter=True)
        .join(NeedCatalog, NeedCatalog.id == Need.catalog_id, isouter=True)
        .order_by(Allocation.created_at.desc())
        .limit(limit)
    ).all()

    return {
        "items": [
            {
                "allocation_id": str(a.id),
                "status": a.status.value,
                "expires_at": a.expires_at,
                "case_code": case_code,
                "catalog_name": c.name_es if c else None,
                "allocated_qty": _num(it.allocated_qty),
                "fulfilled_qty": _num(it.fulfilled_qty),
                "unit": it.unit_code,
                "created_at": a.created_at,
            }
            for a, it, c, case_code in rows
        ]
    }
