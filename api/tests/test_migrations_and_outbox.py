"""Verifica migraciones completas, seeds y el ciclo del outbox.

Requiere PostgreSQL+PostGIS (se salta si no hay servidor).
"""

import sqlalchemy as sa

EXPECTED_TABLES = {
    # identity
    "incidents", "organizations", "users", "roles", "organization_memberships",
    "user_role_assignments", "persons", "person_identifiers", "consents",
    "households", "household_members", "locations", "geo_divipola",
    # intake
    "channels", "conversations", "conversation_participants", "messages",
    "media_assets", "ai_extraction_runs", "extraction_candidates",
    "human_confirmations", "agent_queue_items", "agent_assignments",
    "webhook_receipts", "outbox_events", "idempotency_keys", "notifications",
    # cases
    "reports", "report_subjects", "cases", "case_persons", "case_status_history",
    "need_catalog", "needs", "need_status_history", "validations",
    "evidence", "evidence_links", "duplicate_candidates",
    # supply
    "resource_offers", "resource_offer_items", "money_offer_details",
    "service_offer_details", "transport_offer_details", "volunteer_offer_details",
    "matches", "allocations", "allocation_items", "external_payment_refs",
    # fulfillment
    "shipments", "shipment_stops", "deliveries", "delivery_items",
    "delivery_receipts",
    # trust
    "trust_assessments", "risk_signals", "audit_events", "access_events",
    "retention_jobs",
    # public
    "public_case_profiles", "public_story_items", "public_impact_snapshots",
    "content_reports",
}


def test_all_tables_created(migrated_engine):
    inspector = sa.inspect(migrated_engine)
    existing = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - existing
    assert not missing, f"Faltan tablas: {sorted(missing)}"


def test_seeds_idempotent(migrated_engine, db_session):
    from app.seeds.__main__ import (
        seed_divipola,
        seed_incident,
        seed_need_catalog,
        seed_roles,
    )

    seed_roles(db_session)
    seed_incident(db_session)
    n1 = seed_divipola(db_session)
    seed_need_catalog(db_session)
    db_session.commit()

    # Segunda pasada: no debe duplicar nada
    assert seed_roles(db_session) == 0
    assert seed_incident(db_session) is False
    assert seed_divipola(db_session) == 0
    assert seed_need_catalog(db_session) == 0
    db_session.commit()

    assert n1 == 0 or n1 >= 1100  # DIVIPOLA completo (primera vez) o ya cargado
    total = db_session.execute(sa.text("SELECT count(*) FROM geo_divipola")).scalar()
    assert total >= 1100
    cat = db_session.execute(sa.text("SELECT count(*) FROM need_catalog")).scalar()
    assert cat >= 40


def test_outbox_publish_and_process(migrated_engine, db_session):
    from sqlalchemy.orm import sessionmaker

    from app.core import outbox
    from app.core.ids import new_id

    processed: list[str] = []

    def handler(session, event):
        processed.append(event.event_type)

    outbox.HANDLERS.clear()
    outbox.register_handler("test.event", handler)

    outbox.publish(
        db_session,
        event_type="test.event",
        aggregate_type="test",
        aggregate_id=new_id(),
        payload={"n": 1},
    )
    db_session.commit()

    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    handled = outbox.process_batch(factory)
    assert handled >= 1
    assert "test.event" in processed

    # Idempotencia del claim: nada pendiente en la segunda pasada
    assert outbox.process_batch(factory) == 0


def test_outbox_dead_letter_after_retries(migrated_engine):
    from sqlalchemy.orm import sessionmaker

    from app.core import outbox
    from app.core.ids import new_id
    from app.modules.intake.models import OutboxEvent, OutboxStatus

    def bad_handler(session, event):
        raise ValueError("boom")

    outbox.HANDLERS.clear()
    outbox.register_handler("test.bad", bad_handler)

    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    session = factory()
    ev = outbox.publish(
        session,
        event_type="test.bad",
        aggregate_type="test",
        aggregate_id=new_id(),
    )
    session.commit()
    ev_id = ev.id
    session.close()

    # Forzar reintentos hasta DLQ
    for _ in range(outbox.MAX_RETRIES + 1):
        s = factory()
        row = s.get(OutboxEvent, ev_id)
        row.next_attempt_at = None  # saltar backoff para la prueba
        s.commit()
        s.close()
        outbox.process_batch(factory)

    s = factory()
    row = s.get(OutboxEvent, ev_id)
    assert row.publish_status == OutboxStatus.DEAD_LETTER
    assert row.retry_count >= outbox.MAX_RETRIES
    s.close()
