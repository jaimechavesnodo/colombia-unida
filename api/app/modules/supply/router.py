"""API /v1 de ofertas, matching y asignaciones (§11.1, §14).

Errores de negocio en formato problem+json (§14.3); RBAC deny-by-default
con require_roles (§13.2). Sin PII de casos en las respuestas de
matching: solo case_code, municipio (admin2) y cantidades.
"""

import logging
import uuid
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.db import get_db
from app.core.ids import new_id
from app.core.state_machines import InvalidTransition
from app.modules.cases.models import Case, Need
from app.modules.identity.models import Incident, IncidentStatus, Location, RoleCode, User
from app.modules.supply.matching import generate_matches
from app.modules.supply.models import (
    Allocation,
    AllocationItem,
    Match,
    ResourceOffer,
    ResourceType,
)
from app.modules.supply.service import (
    IdempotencyConflict,
    QuantityUnavailable,
    UnknownCatalogCode,
    cancel_allocation,
    confirm_offer,
    reject_match,
    reserve_allocation,
)

logger = logging.getLogger("supply.api")

router = APIRouter(prefix="/v1", tags=["supply"])

PROBLEM_BASE = "https://colombiaunida.org/problems"

_operativos = require_roles(RoleCode.AGENT, RoleCode.SUPERVISOR)
_supervisor = require_roles(RoleCode.SUPERVISOR)


def _problem(status: int, slug: str, title: str, detail: str, **extra) -> JSONResponse:
    """Respuesta problem+json (§14.3)."""
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": f"{PROBLEM_BASE}/{slug}",
            "title": title,
            "status": status,
            "detail": detail,
            **extra,
        },
    )


def _num(value) -> float | None:
    return float(value) if value is not None else None


# ── Ofertas ────────────────────────────────────────────────────────────


class OfferItemIn(BaseModel):
    catalog_code: str
    quantity: Decimal = Field(gt=0)
    condition: str | None = None


class OfferIn(BaseModel):
    incident_id: uuid.UUID | None = None
    type: ResourceType = ResourceType.IN_KIND
    items: list[OfferItemIn] = Field(min_length=1)
    donor_person_id: uuid.UUID | None = None


@router.post("/resource-offers", status_code=201)
def create_resource_offer(
    body: OfferIn,
    user: User = Depends(_operativos),
    db: Session = Depends(get_db),
):
    incident_id = body.incident_id
    if incident_id is None:
        incident_id = db.execute(
            sa.select(Incident.id)
            .where(Incident.status == IncidentStatus.ACTIVE)
            .order_by(Incident.starts_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    if incident_id is None:
        return _problem(
            422, "no-active-incident", "Sin incidente activo",
            "No hay incidente activo y no se indicó incident_id",
        )
    offer = ResourceOffer(
        id=new_id(),
        incident_id=incident_id,
        type=body.type,
        donor_person_id=body.donor_person_id,
    )
    db.add(offer)
    db.flush()
    try:
        items = confirm_offer(
            db, offer, [i.model_dump(mode="python") for i in body.items], user
        )
    except UnknownCatalogCode as exc:
        db.rollback()
        return _problem(
            422, "unknown-catalog-code", "Código de catálogo desconocido", str(exc)
        )
    db.commit()
    return {
        "id": str(offer.id),
        "status": offer.status.value,
        "incident_id": str(offer.incident_id),
        "items": [
            {
                "id": str(i.id),
                "quantity": _num(i.quantity),
                "unit_code": i.unit_code,
                "condition": i.condition.value,
            }
            for i in items
        ],
    }


@router.get("/resource-offers/{offer_id}/matches")
def list_offer_matches(
    offer_id: uuid.UUID,
    user: User = Depends(_operativos),
    db: Session = Depends(get_db),
):
    offer = db.get(ResourceOffer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    rows = db.execute(
        sa.select(Match, Need, Case.case_code, Location.admin2)
        .join(Need, Need.id == Match.need_id)
        .join(Case, Case.id == Need.case_id)
        .join(Location, Location.id == Case.primary_location_id, isouter=True)
        .where(Match.offer_id == offer_id)
        .order_by(Match.final_rank.desc(), Match.generated_at)
    ).all()
    # Sin PII: solo case_code, municipio (admin2) y cantidades (§10.2).
    return {
        "offer_id": str(offer_id),
        "matches": [
            {
                "id": str(m.id),
                "status": m.status.value,
                "need_id": str(m.need_id),
                "offer_item_id": str(m.offer_item_id) if m.offer_item_id else None,
                "case_code": case_code,
                "municipality": admin2,
                "requested_qty": _num(need.requested_qty),
                "covered_qty": _num(need.covered_qty),
                "unit_code": need.unit_code,
                "scores": {
                    "humanitarian_priority": _num(m.humanitarian_priority_score),
                    "feasibility": _num(m.feasibility_score),
                    "final_rank": _num(m.final_rank),
                },
                "explanation": m.explanation_json,
                "algorithm_version": m.algorithm_version,
            }
            for m, need, case_code, admin2 in rows
        ],
    }


# ── Matching ───────────────────────────────────────────────────────────


class GenerateIn(BaseModel):
    need_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None


@router.post("/matches:generate")
def generate_matches_endpoint(
    body: GenerateIn,
    user: User = Depends(_operativos),
    db: Session = Depends(get_db),
):
    need = offer = None
    if body.need_id is not None:
        need = db.get(Need, body.need_id)
        if need is None:
            raise HTTPException(status_code=404, detail="Necesidad no encontrada")
    if body.offer_id is not None:
        offer = db.get(ResourceOffer, body.offer_id)
        if offer is None:
            raise HTTPException(status_code=404, detail="Oferta no encontrada")
    created = generate_matches(db, need=need, offer=offer)
    db.commit()
    return {
        "created": [
            {
                "id": str(m.id),
                "need_id": str(m.need_id),
                "offer_id": str(m.offer_id),
                "offer_item_id": str(m.offer_item_id) if m.offer_item_id else None,
                "status": m.status.value,
                "final_rank": _num(m.final_rank),
                "algorithm_version": m.algorithm_version,
            }
            for m in created
        ]
    }


class RejectMatchIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/matches/{match_id}:reject")
def reject_match_endpoint(
    match_id: uuid.UUID,
    body: RejectMatchIn,
    user: User = Depends(_operativos),
    db: Session = Depends(get_db),
):
    """Descarta una propuesta de matching.

    El motivo es obligatorio: sin motivo no queda trazabilidad de por qué
    el equipo no atendió esa combinación necesidad–oferta.
    """
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match no encontrado")
    try:
        reject_match(db, match, user.id, body.reason)
        db.commit()
    except InvalidTransition as exc:
        db.rollback()
        return _problem(409, "invalid-state-transition", "Transición inválida", str(exc))
    return {"id": str(match.id), "status": match.status.value}


# ── Asignaciones ───────────────────────────────────────────────────────


class AllocationIn(BaseModel):
    match_id: uuid.UUID
    quantity: Decimal = Field(gt=0)


def _allocation_payload(db: Session, allocation: Allocation) -> dict:
    qty = db.execute(
        sa.select(sa.func.sum(AllocationItem.allocated_qty)).where(
            AllocationItem.allocation_id == allocation.id
        )
    ).scalar()
    return {
        "id": str(allocation.id),
        "match_id": str(allocation.match_id) if allocation.match_id else None,
        "offer_id": str(allocation.offer_id),
        "status": allocation.status.value,
        "quantity": _num(qty),
        "expires_at": allocation.expires_at.isoformat() if allocation.expires_at else None,
    }


@router.post("/allocations", status_code=201)
def create_allocation(
    body: AllocationIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(_operativos),
    db: Session = Depends(get_db),
):
    match = db.get(Match, body.match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match no encontrado")
    try:
        allocation = reserve_allocation(
            db, match, body.quantity, user.id, idempotency_key
        )
        db.commit()
    except QuantityUnavailable as exc:
        db.rollback()
        return _problem(
            409,
            "quantity-no-longer-available",
            "Cantidad ya no disponible",
            f"La cantidad solicitada excede la libre; disponible: {exc.available}",
            available=float(exc.available),
        )
    except InvalidTransition as exc:
        db.rollback()
        return _problem(
            409,
            "invalid-state-transition",
            "Propuesta no reservable",
            str(exc),
        )
    except IdempotencyConflict:
        db.rollback()
        return _problem(
            409,
            "idempotency-conflict",
            "Conflicto de idempotencia",
            "La Idempotency-Key ya fue usada con un request distinto",
        )
    return _allocation_payload(db, allocation)


class CancelIn(BaseModel):
    reason: str = "operator_cancelled"


@router.post("/allocations/{allocation_id}:cancel")
def cancel_allocation_endpoint(
    allocation_id: uuid.UUID,
    body: CancelIn | None = None,
    user: User = Depends(_supervisor),
    db: Session = Depends(get_db),
):
    allocation = db.get(Allocation, allocation_id)
    if allocation is None:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    reason = body.reason if body is not None else "operator_cancelled"
    try:
        cancel_allocation(db, allocation, user.id, reason)
        db.commit()
    except InvalidTransition:
        db.rollback()
        return _problem(
            409,
            "invalid-state-transition",
            "Transición inválida",
            f"No se puede cancelar una asignación en estado {allocation.status.value}",
        )
    return _allocation_payload(db, allocation)
