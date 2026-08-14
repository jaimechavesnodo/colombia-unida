"""M5 — ofertas, matching y asignación (§10, §11.1, MATCH-01, MATCH-02)."""

import os
import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="module", autouse=True)
def _env(migrated_engine):
    from app.core.config import get_settings

    get_settings.cache_clear()
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    s = factory()
    from app.seeds.__main__ import seed_incident, seed_need_catalog, seed_roles

    seed_roles(s)
    seed_incident(s)
    seed_need_catalog(s)
    # Limpieza del módulo supply para reruns deterministas sobre la BD dedicada.
    s.execute(sa.text("DELETE FROM external_payment_refs"))
    s.execute(sa.text("DELETE FROM allocation_items"))
    s.execute(sa.text("DELETE FROM allocations"))
    s.execute(sa.text("DELETE FROM matches"))
    s.execute(sa.text("DELETE FROM resource_offer_items"))
    s.execute(sa.text("DELETE FROM resource_offers"))
    s.execute(sa.text("DELETE FROM idempotency_keys WHERE scope = 'allocations'"))
    s.commit()
    s.close()
    yield
    get_settings.cache_clear()


@pytest.fixture
def factory(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def client(migrated_engine, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-supply")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.modules.supply.router import router as supply_router

    app = create_app()
    app.include_router(supply_router)
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


# ── Helpers ────────────────────────────────────────────────────────────


def _incident_id(s):
    from app.modules.identity.models import Incident

    return s.execute(sa.select(Incident.id).limit(1)).scalar_one()


def _catalog(s, code):
    from app.modules.cases.models import NeedCatalog

    return s.execute(
        sa.select(NeedCatalog).where(NeedCatalog.code == code, NeedCatalog.active.is_(True))
    ).scalars().first()


def _mk_user(s, *role_codes):
    from app.modules.identity.models import Role, User, UserRoleAssignment, UserStatus

    user = User(status=UserStatus.ACTIVE, email_hmac=os.urandom(32))
    s.add(user)
    s.flush()
    for code in role_codes:
        role_id = s.execute(sa.select(Role.id).where(Role.code == code)).scalars().first()
        s.add(UserRoleAssignment(user_id=user.id, role_id=role_id))
    s.flush()
    return user


def _mk_need(s, catalog_code="SHELTER.MATTRESS", qty=10):
    from app.core.ids import new_short_code
    from app.modules.cases.models import (
        Case,
        CaseStatus,
        Need,
        NeedHorizon,
        NeedStatus,
        UrgencyLevel,
    )

    cat = _catalog(s, catalog_code)
    case = Case(
        incident_id=_incident_id(s),
        case_code=new_short_code("CU"),
        case_type="HOUSEHOLD",
        status=CaseStatus.ACTIVE,
    )
    s.add(case)
    s.flush()
    need = Need(
        case_id=case.id,
        catalog_id=cat.id,
        horizon=NeedHorizon.EMERGENCY,
        status=NeedStatus.OPEN,
        requested_qty=Decimal(qty),
        unit_code=cat.unit_code,
        urgency=UrgencyLevel.HIGH,
    )
    s.add(need)
    s.flush()
    return case, need


def _mk_offer(s, catalog_code="SHELTER.MATTRESS", qty=10):
    from app.modules.supply.models import (
        OfferStatus,
        ResourceOffer,
        ResourceOfferItem,
        ResourceType,
    )

    cat = _catalog(s, catalog_code)
    offer = ResourceOffer(
        incident_id=_incident_id(s), type=ResourceType.IN_KIND, status=OfferStatus.AVAILABLE
    )
    s.add(offer)
    s.flush()
    item = ResourceOfferItem(
        offer_id=offer.id, catalog_id=cat.id, quantity=Decimal(qty), unit_code=cat.unit_code
    )
    s.add(item)
    s.flush()
    return offer, item


# ── MATCH-01: elegibilidad dura y explicación ──────────────────────────


def test_matching_eligibility_and_explained_scores(factory):
    from app.modules.intake.models import OutboxEvent
    from app.modules.supply import matching

    s = factory()
    _case, need = _mk_need(s, "SHELTER.MATTRESS", 10)
    water_offer, water_item = _mk_offer(s, "WATER.BOTTLED", 100)
    mattress_offer, mattress_item = _mk_offer(s, "SHELTER.MATTRESS", 10)
    s.commit()

    # Catálogos incompatibles → NO se crea match (MATCH-01)
    assert matching.generate_matches(s, need=need, offer=water_offer) == []

    created = matching.generate_matches(s, need=need, offer=mattress_offer)
    s.commit()
    assert len(created) == 1
    m = created[0]
    assert m.offer_item_id == mattress_item.id
    assert m.eligibility_passed is True
    assert m.algorithm_version == "v1"
    for score in (m.humanitarian_priority_score, m.feasibility_score, m.final_rank):
        assert 0.0 <= float(score) <= 1.0
    exp = m.explanation_json
    assert exp["policy"] == matching.POLICY_VERSION
    assert set(exp["priority_components"]) == {
        "urgency", "vulnerability", "waiting", "coverage_gap", "territorial_equity",
    }
    assert set(exp["feasibility_components"]) == {
        "compatibility", "quantity_fit", "logistics_fit", "time_fit", "cost_fit",
    }

    # Par activo existente → no se duplica
    assert matching.generate_matches(s, need=need, offer=mattress_offer) == []

    n_events = s.execute(
        sa.select(sa.func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == matching.EVENT_MATCH_PROPOSED,
            OutboxEvent.aggregate_id == m.id,
        )
    ).scalar()
    assert n_events == 1
    s.close()


# ── MATCH-02: reservas serializadas por lock de fila ───────────────────


def test_reserve_then_second_session_conflicts(factory):
    from app.modules.cases.models import NeedStatus
    from app.modules.identity.models import RoleCode
    from app.modules.supply import matching, service
    from app.modules.supply.models import AllocationStatus, Match, MatchStatus, OfferStatus

    s = factory()
    user = _mk_user(s, RoleCode.AGENT)
    _case, need = _mk_need(s, qty=10)
    offer, item = _mk_offer(s, qty=10)
    s.commit()
    match = matching.generate_matches(s, need=need, offer=offer)[0]
    s.commit()

    alloc = service.reserve_allocation(s, match, Decimal(6), user.id)
    s.commit()
    assert alloc.status == AllocationStatus.RESERVED
    assert alloc.expires_at is not None
    s.refresh(item), s.refresh(need), s.refresh(offer), s.refresh(match)
    assert item.reserved_qty == Decimal(6)
    assert need.covered_qty == Decimal(6)
    assert need.status == NeedStatus.PARTIALLY_COVERED
    assert offer.status == OfferStatus.PARTIALLY_ALLOCATED
    assert match.status == MatchStatus.APPROVED

    s2 = factory()
    match_b = s2.get(Match, match.id)
    with pytest.raises(service.QuantityUnavailable) as exc:
        service.reserve_allocation(s2, match_b, Decimal(6), user.id)
    assert exc.value.available == Decimal(4)
    s2.rollback()
    s2.close()
    s.close()


def test_reserve_concurrent_threads_exactly_one_wins(factory):
    from app.modules.identity.models import RoleCode
    from app.modules.supply import matching, service
    from app.modules.supply.models import Match, ResourceOfferItem

    s = factory()
    user = _mk_user(s, RoleCode.AGENT)
    _case, need = _mk_need(s, qty=10)
    offer, item = _mk_offer(s, qty=10)
    s.commit()
    match = matching.generate_matches(s, need=need, offer=offer)[0]
    s.commit()
    match_id, item_id, user_id = match.id, item.id, user.id
    s.close()

    barrier = threading.Barrier(2)
    results: dict[int, tuple] = {}

    def worker(idx: int) -> None:
        ws = factory()
        try:
            m = ws.get(Match, match_id)
            barrier.wait()
            a = service.reserve_allocation(ws, m, Decimal(6), user_id)
            ws.commit()
            results[idx] = ("ok", a.id)
        except service.QuantityUnavailable as e:
            ws.rollback()
            results[idx] = ("conflict", e.available)
        finally:
            ws.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    outcomes = sorted(r[0] for r in results.values())
    assert outcomes == ["conflict", "ok"], results
    loser = next(r for r in results.values() if r[0] == "conflict")
    assert loser[1] == Decimal(4)  # el FOR UPDATE serializó: el 2.º vio libre=4

    check = factory()
    assert check.get(ResourceOfferItem, item_id).reserved_qty == Decimal(6)
    check.close()


# ── Expiración con compensación (§10.3) ────────────────────────────────


def test_expiration_releases_reservation_and_coverage(factory):
    from app.core.model_base import utcnow
    from app.modules.cases.models import NeedStatus
    from app.modules.identity.models import RoleCode
    from app.modules.intake.models import OutboxEvent
    from app.modules.supply import matching, service
    from app.modules.supply.models import AllocationStatus, OfferStatus

    s = factory()
    user = _mk_user(s, RoleCode.AGENT)
    _case, need = _mk_need(s, qty=10)
    offer, item = _mk_offer(s, qty=10)
    s.commit()
    match = matching.generate_matches(s, need=need, offer=offer)[0]
    alloc = service.reserve_allocation(s, match, Decimal(5), user.id)
    s.commit()
    s.refresh(item)
    assert item.reserved_qty == Decimal(5)

    alloc.expires_at = utcnow() - timedelta(minutes=5)
    s.commit()

    expired = service.expire_allocations(s)
    s.commit()
    assert expired >= 1
    s.refresh(alloc), s.refresh(item), s.refresh(need), s.refresh(offer)
    assert alloc.status == AllocationStatus.EXPIRED
    assert item.reserved_qty == Decimal(0)
    assert need.covered_qty == Decimal(0)
    assert need.status == NeedStatus.OPEN
    assert offer.status == OfferStatus.AVAILABLE

    n_events = s.execute(
        sa.select(sa.func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == service.EVENT_ALLOCATION_EXPIRED,
            OutboxEvent.aggregate_id == alloc.id,
        )
    ).scalar()
    assert n_events == 1
    s.close()


# ── API: flujo completo, idempotencia y RBAC ───────────────────────────


def test_api_flow_offer_matches_allocation_idempotency(client, factory):
    from app.core.auth import issue_token
    from app.modules.identity.models import RoleCode
    from app.modules.supply.models import ResourceOfferItem

    s = factory()
    agent = _mk_user(s, RoleCode.AGENT)
    case, need = _mk_need(s, qty=10)
    s.commit()
    h = {"Authorization": f"Bearer {issue_token(agent)}"}

    r = client.post(
        "/v1/resource-offers",
        json={"items": [{"catalog_code": "SHELTER.MATTRESS", "quantity": 10}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "AVAILABLE"
    offer_id = r.json()["id"]

    r = client.post(
        "/v1/matches:generate",
        json={"need_id": str(need.id), "offer_id": offer_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 1
    match_id = r.json()["created"][0]["id"]

    r = client.get(f"/v1/resource-offers/{offer_id}/matches", headers=h)
    assert r.status_code == 200
    m0 = r.json()["matches"][0]
    assert m0["case_code"] == case.case_code
    assert m0["requested_qty"] == 10.0
    assert 0.0 <= m0["scores"]["final_rank"] <= 1.0
    assert "priority_components" in m0["explanation"]
    # Sin PII de casos: solo case_code, municipio y cantidades
    assert "narrative" not in m0 and "person" not in str(m0.keys())

    # Idempotencia: misma key + mismo body → misma allocation, sin doble reserva
    key = uuid.uuid4().hex
    body = {"match_id": match_id, "quantity": 4}
    r1 = client.post("/v1/allocations", json=body, headers={**h, "Idempotency-Key": key})
    assert r1.status_code == 201, r1.text
    r2 = client.post("/v1/allocations", json=body, headers={**h, "Idempotency-Key": key})
    assert r2.status_code == 201
    assert r2.json()["id"] == r1.json()["id"]

    check = factory()
    item_row = check.execute(
        sa.select(ResourceOfferItem).where(
            ResourceOfferItem.offer_id == uuid.UUID(offer_id)
        )
    ).scalar_one()
    assert item_row.reserved_qty == Decimal(4)
    check.close()

    # Misma key, body distinto → 409 idempotency-conflict
    r3 = client.post(
        "/v1/allocations",
        json={"match_id": match_id, "quantity": 5},
        headers={**h, "Idempotency-Key": key},
    )
    assert r3.status_code == 409
    assert r3.headers["content-type"].startswith("application/problem+json")
    assert r3.json()["type"].endswith("idempotency-conflict")

    # Cantidad mayor a la libre → 409 quantity-no-longer-available con available
    r4 = client.post("/v1/allocations", json={"match_id": match_id, "quantity": 7}, headers=h)
    assert r4.status_code == 409
    assert r4.json()["type"].endswith("quantity-no-longer-available")
    assert r4.json()["available"] == 6.0
    s.close()


def test_api_requires_role(client, factory):
    from app.core.auth import issue_token

    s = factory()
    nobody = _mk_user(s)  # sin roles
    s.commit()
    h = {"Authorization": f"Bearer {issue_token(nobody)}"}
    r = client.post(
        "/v1/allocations",
        json={"match_id": str(uuid.uuid4()), "quantity": 1},
        headers=h,
    )
    assert r.status_code == 403
    r = client.post("/v1/resource-offers", json={"items": []}, headers=h)
    assert r.status_code == 403
    s.close()


def test_api_cancel_requires_supervisor_and_compensates(client, factory):
    from app.core.auth import issue_token
    from app.modules.cases.models import NeedStatus
    from app.modules.identity.models import RoleCode
    from app.modules.supply import matching, service

    s = factory()
    supervisor = _mk_user(s, RoleCode.SUPERVISOR)
    agent = _mk_user(s, RoleCode.AGENT)
    _case, need = _mk_need(s, qty=10)
    offer, item = _mk_offer(s, qty=10)
    s.commit()
    match = matching.generate_matches(s, need=need, offer=offer)[0]
    alloc = service.reserve_allocation(s, match, Decimal(5), supervisor.id)
    s.commit()

    # AGENT no puede cancelar
    r = client.post(
        f"/v1/allocations/{alloc.id}:cancel",
        json={"reason": "donor_withdrew"},
        headers={"Authorization": f"Bearer {issue_token(agent)}"},
    )
    assert r.status_code == 403

    h = {"Authorization": f"Bearer {issue_token(supervisor)}"}
    r = client.post(
        f"/v1/allocations/{alloc.id}:cancel", json={"reason": "donor_withdrew"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"
    s.refresh(item), s.refresh(need)
    assert item.reserved_qty == Decimal(0)
    assert need.covered_qty == Decimal(0)
    assert need.status == NeedStatus.OPEN

    # Cancelar dos veces → 409 (estado terminal)
    r = client.post(f"/v1/allocations/{alloc.id}:cancel", json={}, headers=h)
    assert r.status_code == 409
    assert r.json()["type"].endswith("invalid-state-transition")
    s.close()


def test_api_reject_match_is_terminal_and_moves_no_quantity(client, factory):
    """MATCH-03: rechazar descarta la propuesta sin tocar inventario."""
    from app.core.auth import issue_token
    from app.modules.identity.models import RoleCode
    from app.modules.supply import matching

    s = factory()
    agent = _mk_user(s, RoleCode.AGENT)
    _case, need = _mk_need(s, qty=10)
    offer, item = _mk_offer(s, qty=10)
    s.commit()
    match = matching.generate_matches(s, need=need, offer=offer)[0]
    s.commit()
    h = {"Authorization": f"Bearer {issue_token(agent)}"}

    # El motivo es obligatorio: sin él no hay trazabilidad de la decisión.
    r = client.post(f"/v1/matches/{match.id}:reject", json={"reason": "no"}, headers=h)
    assert r.status_code == 422

    r = client.post(
        f"/v1/matches/{match.id}:reject",
        json={"reason": "la familia ya recibió este ítem por otra vía"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"

    # Rechazar no reserva ni cubre nada (el ítem nace sin reserva: None).
    s.refresh(item), s.refresh(need)
    assert (item.reserved_qty or Decimal(0)) == Decimal(0)
    assert (need.covered_qty or Decimal(0)) == Decimal(0)

    # REJECTED es terminal: no se puede reservar sobre un match descartado
    # ni rechazarlo de nuevo.
    r = client.post("/v1/allocations", json={"match_id": str(match.id), "quantity": 2}, headers=h)
    assert r.status_code == 409
    r = client.post(
        f"/v1/matches/{match.id}:reject", json={"reason": "otro motivo válido"}, headers=h
    )
    assert r.status_code == 409
    assert r.json()["type"].endswith("invalid-state-transition")
    s.close()
