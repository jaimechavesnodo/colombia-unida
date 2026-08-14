"""Motor de matching necesidad↔oferta (§10.2, MATCH-01).

Elegibilidad dura primero (catálogo exacto, unidad, cantidad libre,
ventana temporal, mismo incidente); solo los pares elegibles reciben
puntajes. Los pesos y componentes quedan versionados en la política v1
y se explican por completo en `explanation_json` (transparencia §10.2).
"""

import logging
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.model_base import utcnow
from app.core.outbox import publish
from app.modules.cases.models import Case, Need, NeedCatalog, NeedStatus, UrgencyLevel
from app.modules.supply.models import (
    Match,
    MatchStatus,
    OfferStatus,
    ResourceOffer,
    ResourceOfferItem,
)

logger = logging.getLogger("matching")

EVENT_MATCH_PROPOSED = "match.proposed"

# Política v1 (§10.2): pesos fijos, sin atributos protegidos (v1 usa
# valores neutros 0.5 para vulnerabilidad, equidad territorial,
# logística y costo).
POLICY_VERSION = "match-policy-v1"

PRIORITY_WEIGHTS = {
    "urgency": 0.30,
    "vulnerability": 0.20,
    "waiting": 0.20,
    "coverage_gap": 0.20,
    "territorial_equity": 0.10,
}
FEASIBILITY_WEIGHTS = {
    "compatibility": 0.35,
    "quantity_fit": 0.20,
    "logistics_fit": 0.20,
    "time_fit": 0.15,
    "cost_fit": 0.10,
}
FINAL_WEIGHTS = {"priority": 0.6, "feasibility": 0.4}

_URGENCY_SCALE = {
    UrgencyLevel.LOW: 0,
    UrgencyLevel.MEDIUM: 1,
    UrgencyLevel.HIGH: 2,
    UrgencyLevel.CRITICAL: 3,
}

_NEED_ELIGIBLE = (NeedStatus.OPEN, NeedStatus.PARTIALLY_COVERED)
_OFFER_ELIGIBLE = (OfferStatus.AVAILABLE, OfferStatus.PARTIALLY_ALLOCATED)
_ACTIVE_MATCH = (MatchStatus.PROPOSED, MatchStatus.REVIEW_REQUIRED, MatchStatus.APPROVED)

ZERO = Decimal(0)


def _free_qty(item: ResourceOfferItem) -> Decimal:
    return Decimal(str(item.quantity)) - (item.reserved_qty or ZERO) - (
        item.delivered_qty or ZERO
    )


def _priority_components(need: Need, now) -> dict[str, float]:
    urgency = _URGENCY_SCALE[need.urgency] / 3 if need.urgency is not None else 0.5
    waiting_days = max((now - need.created_at).days, 0)
    waiting = min(waiting_days, 30) / 30
    requested = float(need.requested_qty) if need.requested_qty else None
    covered = float(need.covered_qty or 0)
    coverage_gap = 0.0 if not requested else max(0.0, 1.0 - covered / requested)
    return {
        "urgency": round(urgency, 6),
        "vulnerability": 0.5,  # v1: sin atributos protegidos
        "waiting": round(waiting, 6),
        "coverage_gap": round(coverage_gap, 6),
        "territorial_equity": 0.5,  # v1
    }


def _feasibility_components(need: Need, free: Decimal) -> dict[str, float]:
    requested = float(need.requested_qty) if need.requested_qty else None
    quantity_fit = 1.0 if requested is None else min(float(free) / requested, 1.0)
    return {
        "compatibility": 1.0,  # catálogo exacto (elegibilidad ya lo garantizó)
        "quantity_fit": round(quantity_fit, 6),
        "logistics_fit": 0.5,  # v1
        "time_fit": 1.0,  # sin fechas o ventana compatible (filtrado duro)
        "cost_fit": 0.5,  # v1
    }


def _weighted(components: dict[str, float], weights: dict[str, float]) -> float:
    return round(sum(components[k] * w for k, w in weights.items()), 6)


def generate_matches(
    session: Session,
    need: Need | None = None,
    offer: ResourceOffer | None = None,
    algorithm_version: str = "v1",
) -> list[Match]:
    """Genera matches PROPOSED para pares elegibles; devuelve los creados.

    Pares no elegibles no crean Match. Empates: orden estable por
    (rank desc, need.created_at asc, need.id) — §10.3.
    """
    now = utcnow()
    today = now.date()

    need_q = sa.select(Need).where(
        Need.status.in_(_NEED_ELIGIBLE), Need.catalog_id.is_not(None)
    )
    if need is not None:
        need_q = need_q.where(Need.id == need.id)
    needs = session.execute(need_q).scalars().all()
    if not needs:
        return []

    item_q = (
        sa.select(ResourceOfferItem, ResourceOffer)
        .join(ResourceOffer, ResourceOfferItem.offer_id == ResourceOffer.id)
        .where(ResourceOffer.status.in_(_OFFER_ELIGIBLE))
    )
    if offer is not None:
        item_q = item_q.where(ResourceOffer.id == offer.id)
    pairs = session.execute(item_q).all()
    if not pairs:
        return []

    catalog_ids = {n.catalog_id for n in needs} | {
        i.catalog_id for i, _ in pairs if i.catalog_id is not None
    }
    catalogs = {
        c.id: c
        for c in session.execute(
            sa.select(NeedCatalog).where(NeedCatalog.id.in_(catalog_ids))
        ).scalars()
    }
    case_incident = dict(
        session.execute(
            sa.select(Case.id, Case.incident_id).where(
                Case.id.in_({n.case_id for n in needs})
            )
        ).all()
    )
    existing = {
        (row.need_id, row.offer_item_id)
        for row in session.execute(
            sa.select(Match.need_id, Match.offer_item_id).where(
                Match.need_id.in_([n.id for n in needs]),
                Match.status.in_(_ACTIVE_MATCH),
            )
        ).all()
    }

    candidates: list[tuple[float, Need, ResourceOfferItem, ResourceOffer, dict, dict]] = []
    for n in needs:
        need_cat = catalogs.get(n.catalog_id)
        if need_cat is None:
            continue
        for item, item_offer in pairs:
            # ── Elegibilidad dura (MATCH-01) ──────────────────────────
            item_cat = catalogs.get(item.catalog_id)
            if item_cat is None or item_cat.code != need_cat.code:
                continue  # catálogos distintos → NO match
            need_unit = n.unit_code or need_cat.unit_code
            item_unit = item.unit_code or item_cat.unit_code
            if need_unit != item_unit:
                continue
            free = _free_qty(item)
            if free <= 0:
                continue
            if item_offer.available_to is not None and item_offer.available_to < now:
                continue
            if n.needed_by is not None and n.needed_by < today:
                continue
            if case_incident.get(n.case_id) != item_offer.incident_id:
                continue
            if (n.id, item.id) in existing:
                continue

            priority_c = _priority_components(n, now)
            feasibility_c = _feasibility_components(n, free)
            priority = _weighted(priority_c, PRIORITY_WEIGHTS)
            feasibility = _weighted(feasibility_c, FEASIBILITY_WEIGHTS)
            rank = round(
                FINAL_WEIGHTS["priority"] * priority
                + FINAL_WEIGHTS["feasibility"] * feasibility,
                6,
            )
            candidates.append((rank, n, item, item_offer, priority_c, feasibility_c))

    candidates.sort(key=lambda c: (-c[0], c[1].created_at, str(c[1].id)))

    created: list[Match] = []
    for rank, n, item, item_offer, priority_c, feasibility_c in candidates:
        priority = _weighted(priority_c, PRIORITY_WEIGHTS)
        feasibility = _weighted(feasibility_c, FEASIBILITY_WEIGHTS)
        match = Match(
            id=new_id(),
            need_id=n.id,
            offer_id=item_offer.id,
            offer_item_id=item.id,
            algorithm_version=algorithm_version,
            eligibility_passed=True,
            humanitarian_priority_score=priority,
            feasibility_score=feasibility,
            final_rank=rank,
            explanation_json={
                "policy": POLICY_VERSION,
                "priority_components": priority_c,
                "feasibility_components": feasibility_c,
                "weights": {
                    "priority": PRIORITY_WEIGHTS,
                    "feasibility": FEASIBILITY_WEIGHTS,
                    "final": FINAL_WEIGHTS,
                },
            },
            status=MatchStatus.PROPOSED,
            generated_at=now,
        )
        session.add(match)
        publish(
            session,
            event_type=EVENT_MATCH_PROPOSED,
            aggregate_type="match",
            aggregate_id=match.id,
            payload={
                "match_id": str(match.id),
                "need_id": str(n.id),
                "offer_id": str(item_offer.id),
                "final_rank": rank,
                "algorithm_version": algorithm_version,
            },
        )
        created.append(match)
    return created
