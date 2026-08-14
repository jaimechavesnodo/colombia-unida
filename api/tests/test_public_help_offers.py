"""Ofrecimientos de ayuda desde la web (§6.3, §11.1, PRIV-01).

Lo que se protege aquí: que una oferta web no entre como recurso disponible,
que el dinero quede como promesa sin custodia, que el contacto de quien ofrece
no vuelva nunca en una respuesta, y que el formulario no sirva para averiguar
qué casos existen.
"""

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
    s.commit()
    s.close()
    yield
    get_settings.cache_clear()


@pytest.fixture
def factory(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def client(migrated_engine):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _published_profile(s):
    """Caso con perfil público publicado y una necesidad con brecha."""
    from app.core.ids import new_id, new_short_code
    from app.modules.cases.models import (
        Case,
        CaseStatus,
        Need,
        NeedCatalog,
        NeedHorizon,
        NeedStatus,
    )
    from app.modules.identity.models import Incident
    from app.modules.public_impact.models import (
        ConsentBasis,
        PublicationStatus,
        PublicCaseProfile,
    )

    incident = s.execute(sa.select(Incident).limit(1)).scalar_one()
    catalog = s.execute(
        sa.select(NeedCatalog).where(NeedCatalog.code == "SHELTER.BLANKET")
    ).scalar_one()

    case = Case(
        id=new_id(),
        incident_id=incident.id,
        case_code=new_short_code("CU"),
        status=CaseStatus.ACTIVE,
        opened_at=sa.func.now(),
    )
    s.add(case)
    s.flush()
    s.add(
        Need(
            id=new_id(),
            case_id=case.id,
            catalog_id=catalog.id,
            horizon=catalog.default_horizon or NeedHorizon.EMERGENCY,
            status=NeedStatus.OPEN,
            requested_qty=Decimal(10),
            confirmed_qty=Decimal(10),
            covered_qty=Decimal(4),
            unit_code=catalog.unit_code,
        )
    )
    slug = f"caso-de-prueba-{str(case.id)[-6:]}"
    s.add(
        PublicCaseProfile(
            id=new_id(),
            case_id=case.id,
            slug=slug,
            display_title="Familia necesita cobijas",
            story_summary="Resumen público sin datos identificables.",
            coarse_location={"admin1": "CALDAS", "admin2": "NEIRA"},
            household_size_band="3-5",
            progress_percent=Decimal(40),
            publication_status=PublicationStatus.PUBLISHED,
            consent_basis=ConsentBasis.EXPLICIT_CONSENT,
            published_at=sa.func.now(),
        )
    )
    s.commit()
    return slug


def test_help_options_muestra_brecha_sin_datos_del_hogar(client, factory):
    s = factory()
    slug = _published_profile(s)
    s.close()

    r = client.get(f"/public/v1/cases/{slug}/help-options")
    assert r.status_code == 200, r.text
    options = r.json()["options"]
    assert len(options) == 1
    assert options[0]["catalog_code"] == "SHELTER.BLANKET"
    assert options[0]["pending_qty"] == 6.0  # 10 confirmadas - 4 cubiertas
    # Nada del hogar ni de las personas
    plano = r.text.lower()
    for prohibido in ("phone", "person", "narrative", "household_id", "case_id"):
        assert prohibido not in plano


def test_oferta_web_queda_pendiente_de_confirmacion(client, factory):
    """La oferta no puede nacer disponible: la confirma una persona."""
    from app.modules.supply.models import OfferStatus, ResourceOffer

    s = factory()
    slug = _published_profile(s)
    s.close()

    r = client.post(
        "/public/v1/help-offers",
        json={
            "slug": slug,
            "offer_type": "IN_KIND",
            "contact_name": "Ferretería El Cafetal",
            "contact_phone": "3105551234",
            "catalog_code": "SHELTER.BLANKET",
            "quantity": 6,
            "consent_contact": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "received"
    assert len(body["reference"]) == 8

    # La respuesta no devuelve PII ni identificadores internos.
    assert "3105551234" not in r.text
    assert "Cafetal" not in r.text
    for prohibido in ("offer_id", "person_id", "case_id"):
        assert prohibido not in r.text

    check = factory()
    offer = check.execute(
        sa.select(ResourceOffer).order_by(ResourceOffer.created_at.desc()).limit(1)
    ).scalar_one()
    assert offer.status == OfferStatus.PENDING_CONFIRMATION
    check.close()


def test_oferta_web_no_entra_al_matching(client, factory):
    """Una oferta pendiente no debe producir candidatos de matching."""
    from app.modules.supply.matching import generate_matches
    from app.modules.supply.models import ResourceOffer

    s = factory()
    slug = _published_profile(s)
    s.close()

    client.post(
        "/public/v1/help-offers",
        json={
            "slug": slug,
            "offer_type": "IN_KIND",
            "contact_name": "Donante Web",
            "contact_phone": "3105557777",
            "catalog_code": "SHELTER.BLANKET",
            "quantity": 6,
            "consent_contact": True,
        },
    )

    s = factory()
    offer = s.execute(
        sa.select(ResourceOffer).order_by(ResourceOffer.created_at.desc()).limit(1)
    ).scalar_one()
    assert generate_matches(s, offer=offer) == []
    s.close()


def test_dinero_es_promesa_sin_custodia(client, factory):
    from app.modules.supply.models import MoneyOfferDetail

    s = factory()
    slug = _published_profile(s)
    s.close()

    r = client.post(
        "/public/v1/help-offers",
        json={
            "slug": slug,
            "offer_type": "MONEY",
            "contact_name": "Donante Anónimo",
            "contact_phone": "3105558888",
            "amount_cop": 500000,
            "consent_contact": True,
        },
    )
    assert r.status_code == 201, r.text

    check = factory()
    detail = check.execute(
        sa.select(MoneyOfferDetail).order_by(MoneyOfferDetail.created_at.desc()).limit(1)
    ).scalar_one()
    assert detail.pledged_amount == Decimal(500000)
    assert detail.currency == "COP"
    # La plataforma no custodia fondos (decisión O2).
    assert detail.settlement_required is False
    check.close()


def test_sin_consentimiento_se_rechaza(client):
    r = client.post(
        "/public/v1/help-offers",
        json={
            "offer_type": "IN_KIND",
            "contact_name": "Sin permiso",
            "contact_phone": "3105551111",
            "quantity": 2,
            "consent_contact": False,
        },
    )
    assert r.status_code == 422
    assert "autorizaci" in r.json()["detail"].lower()


def test_slug_inexistente_no_revela_si_el_caso_existe(client, factory):
    """Mismo resultado para un caso que no existe y para uno no publicado.

    Si el formulario respondiera distinto, serviría para enumerar casos.
    """
    from app.modules.supply.models import MoneyOfferDetail

    payload = {
        "offer_type": "MONEY",
        "contact_name": "Donante Curioso",
        "contact_phone": "3105552222",
        "amount_cop": 100000,
        "consent_contact": True,
    }
    r1 = client.post("/public/v1/help-offers", json={**payload, "slug": "no-existe-jamas"})
    assert r1.status_code == 201
    assert r1.json()["status"] == "received"

    check = factory()
    detail = check.execute(
        sa.select(MoneyOfferDetail).order_by(MoneyOfferDetail.created_at.desc()).limit(1)
    ).scalar_one()
    # Se registró, pero sin designar caso.
    assert detail.designated_case_id is None
    check.close()

    r2 = client.get("/public/v1/cases/no-existe-jamas/help-options")
    assert r2.status_code == 200
    assert r2.json()["options"] == []


def test_contacto_se_guarda_cifrado_y_deduplica_por_telefono(client, factory):
    """Dos ofertas del mismo teléfono son un solo donante, con su correo."""
    from app.core.security import decrypt_text, phone_hmac
    from app.modules.identity.models import IdentifierType, PersonIdentifier

    telefono = "3105553333"
    base = {
        "offer_type": "IN_KIND",
        "contact_name": "Depósito La Esperanza",
        "contact_phone": telefono,
        "quantity": 4,
        "consent_contact": True,
    }
    assert client.post("/public/v1/help-offers", json=base).status_code == 201
    # La segunda vez agrega correo: el dato nuevo no se puede perder por
    # haber reconocido a la persona.
    assert (
        client.post(
            "/public/v1/help-offers",
            json={**base, "contact_email": "compras@ejemplo.co"},
        ).status_code
        == 201
    )

    s = factory()
    phones = (
        s.execute(
            sa.select(PersonIdentifier).where(
                PersonIdentifier.type == IdentifierType.PHONE,
                PersonIdentifier.value_hmac == phone_hmac(telefono),
            )
        )
        .scalars()
        .all()
    )
    assert len(phones) == 1  # un solo donante, no uno por oferta
    person_id = phones[0].person_id
    # Se guarda normalizado a E.164 sin '+' (formato wa_id de Meta), para que
    # el mismo número escrito de dos formas sea el mismo donante y sirva para
    # escribirle por WhatsApp sin más conversiones.
    assert decrypt_text(phones[0].value_enc) == "57" + telefono

    emails = (
        s.execute(
            sa.select(PersonIdentifier).where(
                PersonIdentifier.person_id == person_id,
                PersonIdentifier.type == IdentifierType.EMAIL,
            )
        )
        .scalars()
        .all()
    )
    assert len(emails) == 1
    assert decrypt_text(emails[0].value_enc) == "compras@ejemplo.co"
    s.close()


def test_consentimiento_de_contacto_queda_registrado(client, factory):
    from app.modules.identity.models import ChannelType, Consent, ConsentPurpose, ConsentStatus

    assert (
        client.post(
            "/public/v1/help-offers",
            json={
                "offer_type": "VOLUNTEERING",
                "contact_name": "Voluntaria",
                "contact_phone": "3105554444",
                "quantity": 8,
                "consent_contact": True,
            },
        ).status_code
        == 201
    )

    s = factory()
    consent = s.execute(
        sa.select(Consent)
        .where(Consent.captured_via == ChannelType.WEB)
        .order_by(Consent.created_at.desc())
        .limit(1)
    ).scalar_one()
    assert consent.purpose == ConsentPurpose.CONTACT
    assert consent.status == ConsentStatus.GRANTED
    assert consent.granted_at is not None
    s.close()


def test_campos_desconocidos_se_rechazan(client):
    """extra=forbid: el formulario público no acepta llaves que no espera."""
    r = client.post(
        "/public/v1/help-offers",
        json={
            "offer_type": "MONEY",
            "contact_name": "Prueba",
            "contact_phone": "3105555555",
            "amount_cop": 1000,
            "consent_contact": True,
            "status": "AVAILABLE",
        },
    )
    assert r.status_code == 422
