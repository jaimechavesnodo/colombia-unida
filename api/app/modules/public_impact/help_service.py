"""Ofrecimientos de ayuda que entran por el formulario público.

Este módulo es la única escritura del plano público que crea entidades de
dominio, así que conviene ser explícito sobre sus límites:

- La oferta nace en PENDING_CONFIRMATION, nunca AVAILABLE. Un ofrecimiento
  desde una página web no es un recurso disponible: alguien del equipo tiene
  que hablar con quien ofrece antes de que el matching lo considere. Esa es la
  misma regla del canal de WhatsApp (§11.1) y aquí no se relaja.
- El dinero se registra como promesa, sin custodia de fondos (decisión O2):
  se guarda el monto prometido y nada más; el recaudo ocurre fuera.
- El contacto de quien ofrece es PII: va cifrado, con HMAC solo para poder
  deduplicar por teléfono. Nunca vuelve en una respuesta.
- La respuesta al navegador no confirma nada del caso más allá de lo que ya
  es público. Si el slug no existe, el ofrecimiento se registra igual sin
  designar caso: así el formulario no sirve para adivinar qué casos existen.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.logging import log_ctx
from app.core.outbox import publish
from app.core.security import encrypt_json, encrypt_text, hmac_index, phone_hmac
from app.modules.cases.models import Need, NeedCatalog
from app.modules.identity.models import (
    ChannelType,
    Consent,
    ConsentPurpose,
    ConsentStatus,
    IdentifierType,
    Incident,
    IncidentStatus,
    Person,
    PersonIdentifier,
)
from app.modules.public_impact.models import PublicationStatus, PublicCaseProfile
from app.modules.supply.models import (
    MoneyOfferDetail,
    OfferStatus,
    ResourceOffer,
    ResourceOfferItem,
    ResourceType,
)

logger = logging.getLogger("public_api.help")

EVENT_HELP_OFFER_RECEIVED = "offer.web_submitted"

# Tipos que el formulario público acepta. Se listan explícitamente en vez de
# aceptar cualquier valor del enum: si mañana aparece un tipo nuevo en el
# dominio, no queda expuesto en la web sin decidirlo.
PUBLIC_OFFER_TYPES = {
    "IN_KIND": ResourceType.IN_KIND,
    "MONEY": ResourceType.MONEY,
    "SERVICE": ResourceType.SERVICE,
    "TRANSPORT": ResourceType.TRANSPORT,
    "VOLUNTEERING": ResourceType.VOLUNTEERING,
}


class HelpOfferRejected(Exception):
    """Datos insuficientes o inconsistentes en el formulario."""


def _active_incident(session: Session) -> Incident | None:
    """La emergencia activa a la que se imputa el ofrecimiento.

    Solo ACTIVE: si la emergencia está en borrador o cerrada, aceptar ayuda
    sería prometerle a alguien que su donación va a alguna parte.
    """
    return session.execute(
        sa.select(Incident)
        .where(Incident.status == IncidentStatus.ACTIVE)
        .order_by(Incident.starts_at.desc().nulls_last())
        .limit(1)
    ).scalar_one_or_none()


def _find_or_create_donor(session: Session, name: str, phone: str, email: str | None) -> Person:
    """Reusa la persona si el teléfono ya ofreció antes; si no, la crea.

    El dedupe va por HMAC de teléfono, no por nombre: quien ofrece dos veces
    escribe su nombre distinto ("Juan P." / "Juan Pérez") pero el teléfono es
    el mismo, y así el equipo ve un solo donante con dos ofertas.
    """
    hmac_value = phone_hmac(phone)
    existing = session.execute(
        sa.select(PersonIdentifier).where(
            PersonIdentifier.type == IdentifierType.PHONE,
            PersonIdentifier.value_hmac == hmac_value,
        )
    ).scalar_one_or_none()

    person = session.get(Person, existing.person_id) if existing is not None else None
    if person is None:
        person = Person(id=new_id(), display_name_enc=encrypt_text(name))
        session.add(person)
        session.flush()
        session.add(
            PersonIdentifier(
                id=new_id(),
                person_id=person.id,
                type=IdentifierType.PHONE,
                value_enc=encrypt_text(phone),
                value_hmac=hmac_value,
                last4=phone[-4:],
                is_primary=True,
            )
        )

    # El correo se agrega también cuando la persona ya existía: quien ofreció
    # antes solo con teléfono y ahora deja correo debe quedar con los dos, no
    # perder el dato nuevo por haber sido reconocido.
    if email:
        email_hmac = hmac_index(email.strip().lower())
        ya_esta = session.execute(
            sa.select(PersonIdentifier.id).where(
                PersonIdentifier.person_id == person.id,
                PersonIdentifier.type == IdentifierType.EMAIL,
                PersonIdentifier.value_hmac == email_hmac,
            )
        ).scalar_one_or_none()
        if ya_esta is None:
            session.add(
                PersonIdentifier(
                    id=new_id(),
                    person_id=person.id,
                    type=IdentifierType.EMAIL,
                    value_enc=encrypt_text(email),
                    value_hmac=email_hmac,
                )
            )
    return person


def _designated_case_id(session: Session, slug: str | None):
    """Caso al que se dirige el ofrecimiento, si el slug es de un perfil publicado."""
    if not slug:
        return None
    profile = session.execute(
        sa.select(PublicCaseProfile).where(
            PublicCaseProfile.slug == slug,
            PublicCaseProfile.publication_status == PublicationStatus.PUBLISHED,
        )
    ).scalar_one_or_none()
    return profile.case_id if profile else None


def create_help_offer(
    session: Session,
    *,
    slug: str | None,
    offer_type: str,
    contact_name: str,
    contact_phone: str,
    contact_email: str | None,
    message: str | None,
    catalog_code: str | None,
    quantity,
    amount_cop,
    consent_contact: bool,
) -> dict:
    """Registra el ofrecimiento y devuelve solo lo que el navegador puede ver."""
    if not consent_contact:
        # Sin autorización de contacto la oferta es inservible: nadie podría
        # llamar a coordinar la entrega.
        raise HelpOfferRejected("Se necesita autorización para contactarte")

    rtype = PUBLIC_OFFER_TYPES.get(offer_type)
    if rtype is None:
        raise HelpOfferRejected("Tipo de ayuda no válido")

    incident = _active_incident(session)
    if incident is None:
        raise HelpOfferRejected("No hay una emergencia activa en este momento")

    if rtype is ResourceType.MONEY and not (amount_cop and amount_cop > 0):
        raise HelpOfferRejected("Indica el monto que quieres aportar")
    if rtype is not ResourceType.MONEY and not (quantity and quantity > 0):
        raise HelpOfferRejected("Indica cuánto puedes aportar")

    donor = _find_or_create_donor(session, contact_name, contact_phone, contact_email)
    case_id = _designated_case_id(session, slug)

    offer = ResourceOffer(
        id=new_id(),
        incident_id=incident.id,
        donor_person_id=donor.id,
        type=rtype,
        # Nace pendiente a propósito: el equipo confirma antes de que el
        # matching pueda contar con este recurso.
        status=OfferStatus.PENDING_CONFIRMATION,
        notes_enc=encrypt_json(
            {
                "origen": "formulario_web",
                "slug_solicitado": slug,
                "mensaje": (message or "")[:1000],
                "nombre_contacto": contact_name,
            }
        ),
    )
    session.add(offer)
    session.flush()

    session.add(
        Consent(
            id=new_id(),
            person_id=donor.id,
            incident_id=incident.id,
            purpose=ConsentPurpose.CONTACT,
            notice_version="aviso-web-v1-borrador",
            status=ConsentStatus.GRANTED,
            captured_via=ChannelType.WEB,
            granted_at=sa.func.now(),
        )
    )

    if rtype is ResourceType.MONEY:
        session.add(
            MoneyOfferDetail(
                offer_id=offer.id,
                currency="COP",
                pledged_amount=amount_cop,
                designated_case_id=case_id,
                # La plataforma no custodia fondos (decisión O2): esto es una
                # promesa y el recaudo se coordina fuera.
                settlement_required=False,
            )
        )
    else:
        catalog = None
        if catalog_code:
            catalog = session.execute(
                sa.select(NeedCatalog).where(NeedCatalog.code == catalog_code)
            ).scalar_one_or_none()
        session.add(
            ResourceOfferItem(
                id=new_id(),
                offer_id=offer.id,
                catalog_id=catalog.id if catalog else None,
                description_redacted=(
                    catalog.name_es if catalog else (message or "Ayuda por definir")[:200]
                ),
                quantity=quantity,
                unit_code=catalog.unit_code if catalog else "UNIT",
            )
        )

    publish(
        session,
        event_type=EVENT_HELP_OFFER_RECEIVED,
        aggregate_type="resource_offer",
        aggregate_id=offer.id,
        payload={
            "offer_id": str(offer.id),
            "type": rtype.value,
            "designated_case": bool(case_id),
            "channel": "WEB",
        },
    )

    log_ctx(
        logger,
        logging.INFO,
        "help offer received",
        offer_type=rtype.value,
        designated=bool(case_id),
    )
    # Sin ids de caso ni de persona: el navegador solo necesita saber que
    # quedó registrado y qué sigue.
    return {
        "status": "received",
        # Los últimos 8 caracteres, no los primeros: el id es UUIDv7 y su
        # prefijo es la marca de tiempo, así que todas las referencias
        # empezarían igual y además revelarían el orden de creación.
        "reference": str(offer.id).replace("-", "")[-8:].upper(),
        "next_step": (
            "Alguien del equipo te contactará al número que dejaste para "
            "coordinar la entrega. Tu ofrecimiento no se publica."
        ),
    }


def case_help_options(session: Session, slug: str) -> list[dict]:
    """Qué se necesita en un caso publicado, para llenar el formulario.

    Lee las necesidades reales del caso pero devuelve solo categoría, unidad y
    cuánto falta — nada que identifique al hogar. Sirve para que el formulario
    ofrezca opciones concretas en vez de un campo libre.
    """
    profile = session.execute(
        sa.select(PublicCaseProfile).where(
            PublicCaseProfile.slug == slug,
            PublicCaseProfile.publication_status == PublicationStatus.PUBLISHED,
        )
    ).scalar_one_or_none()
    if profile is None:
        return []

    rows = session.execute(
        sa.select(Need, NeedCatalog)
        .join(NeedCatalog, NeedCatalog.id == Need.catalog_id, isouter=True)
        .where(Need.case_id == profile.case_id)
        .order_by(Need.created_at)
    ).all()

    options = []
    for need, catalog in rows:
        base = need.confirmed_qty or need.requested_qty or 0
        covered = need.covered_qty or 0
        gap = max(float(base) - float(covered), 0)
        if gap <= 0 or catalog is None:
            continue
        options.append(
            {
                "catalog_code": catalog.code,
                "name": catalog.name_es,
                "unit": catalog.unit_code,
                # Redondeo explícito: la resta de dos Decimal convertidos a
                # float deja restos como 1.2000000000000002, y eso en una
                # página pública se lee como si el sistema estuviera roto.
                "pending_qty": round(gap, 1),
            }
        )
    return options
