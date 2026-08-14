"""Datos de demostración — 100% SINTÉTICOS.

Uso: python -m app.seeds.demo   (requiere seeds base: python -m app.seeds)

Genera un escenario realista para mostrar el sistema: casos del eje
cafetero en distintos estados, necesidades por horizonte, ofertas de
donantes, asignaciones y una entrega verificada. Reproducible
(random con semilla fija) e idempotente (no duplica si ya corrió).

⚠️ Ningún dato corresponde a personas reales (clasificación del alcance:
"no contiene datos reales de beneficiarios"). No ejecutar en un entorno
con datos reales de un incidente activo.
"""

import logging
import random
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.core.db import get_session_factory
from app.core.ids import new_short_code
from app.core.logging import log_ctx, setup_logging
from app.core.model_base import utcnow
from app.core.security import encrypt_json, encrypt_text, phone_hmac
from app.modules.cases.models import (
    Case,
    CasePerson,
    CasePersonRole,
    CaseStatus,
    CaseStatusHistory,
    Need,
    NeedCatalog,
    NeedHorizon,
    NeedStatus,
    Report,
    ReporterRole,
    ReportStatus,
    ReportSubject,
    SubjectType,
)
from app.modules.identity.models import (
    ChannelType,
    Consent,
    ConsentPurpose,
    ConsentStatus,
    GeoDivipola,
    Household,
    IdentifierType,
    Incident,
    IncidentStatus,
    Location,
    LocationSource,
    Person,
    PersonIdentifier,
    RoleCode,  # noqa: E402 (usado en DEMO_USERS)
)
from app.modules.intake.models import (
    Channel,
    ChannelStatus,
    Conversation,
    ConversationIntent,
    ConversationStatus,
    Message,
    MessageDeliveryStatus,
    MessageDirection,
    MessageProcessingStatus,
    MessageType,
    ProviderType,
)
from app.modules.supply.models import (
    Allocation,
    AllocationItem,
    AllocationStatus,
    ItemCondition,
    Match,
    MatchStatus,
    OfferStatus,
    ResourceOffer,
    ResourceOfferItem,
    ResourceType,
)

logger = logging.getLogger("seeds.demo")

DEMO_MARKER = "DEMO"

FIRST_NAMES = [
    "María", "José", "Luz", "Carlos", "Ana", "Luis", "Rosa", "Jorge",
    "Carmen", "Pedro", "Gloria", "Andrés", "Marta", "Julián", "Diana",
]
LAST_NAMES = [
    "García", "Rodríguez", "López", "Martínez", "Gómez", "Ramírez",
    "Cardona", "Ospina", "Valencia", "Giraldo", "Quintero", "Salazar",
]
MUNICIPALITIES = [
    "MANIZALES", "CHINCHINÁ", "VILLAMARÍA", "NEIRA", "PALESTINA",
    "PEREIRA", "SANTA ROSA DE CABAL", "ARMENIA", "CALARCÁ", "SALENTO",
]
NARRATIVES = [
    "Se cayó parte del techo de la casa y dormimos en el patio. "
    "Necesitamos colchones y plástico para cubrirnos.",
    "La pared del fondo se agrietó y el tanque de agua se rompió. "
    "Somos cinco, dos niños pequeños.",
    "La casa quedó inhabitable, estamos donde una vecina. "
    "Necesitamos mercado y cobijas.",
    "El local donde trabajaba quedó destruido, no tenemos ingresos. "
    "Por ahora necesitamos alimentación.",
    "Mi mamá usa silla de ruedas y la rampa se derrumbó. "
    "Necesitamos ayuda para repararla y sus medicamentos.",
]
NEED_PLAN = [
    # (catalog_code, qty, urgencia relativa)
    ("SHELTER.MATTRESS", 3, "HIGH"),
    ("SHELTER.BLANKET", 5, "MEDIUM"),
    ("FOOD.RATION", 2, "CRITICAL"),
    ("WATER.BOTTLED", 20, "HIGH"),
    ("HYGIENE.KIT", 2, "MEDIUM"),
    ("HOUSING.TARP", 12, "HIGH"),
    ("HOUSING.ROOF.REPAIR", 1, "MEDIUM"),
    ("HEALTH.MEDICATION", 1, "CRITICAL"),
]

# Estados objetivo de los casos demo (variedad para la consola y el feed)
CASE_STATES = (
    [CaseStatus.INCOMPLETE] * 3
    + [CaseStatus.PENDING_VERIFICATION] * 4
    + [CaseStatus.VERIFIED] * 2
    + [CaseStatus.ACTIVE] * 4
    + [CaseStatus.PARTIALLY_SERVED] * 1
    + [CaseStatus.SERVED] * 1
)


def _already_seeded(session) -> bool:
    return (
        session.execute(
            sa.select(sa.func.count()).select_from(Case).where(
                Case.case_type == DEMO_MARKER
            )
        ).scalar()
        > 0
    )


def _incident(session) -> Incident:
    inc = session.execute(
        sa.select(Incident).where(Incident.status == IncidentStatus.ACTIVE).limit(1)
    ).scalar_one_or_none()
    if inc is None:
        raise SystemExit("Corre primero los seeds base: python -m app.seeds")
    return inc


def _channel(session) -> Channel:
    ch = session.execute(sa.select(Channel).limit(1)).scalar_one_or_none()
    if ch is None:
        ch = Channel(
            type=ChannelType.WHATSAPP,
            provider=ProviderType.META_CLOUD_API,
            display_name="WhatsApp Colombia Unida (demo)",
            status=ChannelStatus.TEST,
            config={},
        )
        session.add(ch)
        session.flush()
    return ch


def _catalog(session) -> dict[str, NeedCatalog]:
    rows = session.execute(
        sa.select(NeedCatalog).where(NeedCatalog.active.is_(True))
    ).scalars().all()
    return {c.code: c for c in rows}


def _mk_person(session, rng, phone: str) -> Person:
    name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    person = Person(display_name_enc=encrypt_text(name))
    session.add(person)
    session.flush()
    session.add(
        PersonIdentifier(
            person_id=person.id,
            type=IdentifierType.PHONE,
            value_enc=encrypt_text(phone),
            value_hmac=phone_hmac(phone),
            last4=phone[-4:],
            is_primary=True,
        )
    )
    return person


def _mk_location(session, rng) -> tuple[Location, str]:
    muni_name = rng.choice(MUNICIPALITIES)
    muni = session.execute(
        sa.select(GeoDivipola)
        .where(sa.func.upper(GeoDivipola.municipality_name) == muni_name)
        .limit(1)
    ).scalar_one_or_none()
    loc = Location(
        admin1=muni.department_name if muni else "CALDAS",
        admin2=muni.municipality_name if muni else muni_name,
        source=LocationSource.GEOCODED_TEXT,
    )
    session.add(loc)
    session.flush()
    return loc, (muni.municipality_name if muni else muni_name)


def _status_path(target: CaseStatus) -> list[CaseStatus]:
    full = [
        CaseStatus.DRAFT, CaseStatus.INCOMPLETE, CaseStatus.PENDING_VERIFICATION,
        CaseStatus.VERIFIED, CaseStatus.ACTIVE, CaseStatus.PARTIALLY_SERVED,
        CaseStatus.SERVED,
    ]
    return full[: full.index(target) + 1]


def seed_demo(session) -> dict:
    rng = random.Random(42)  # noqa: S311 — datos sintéticos reproducibles, no cripto
    incident = _incident(session)
    channel = _channel(session)
    catalog = _catalog(session)
    stats = {"cases": 0, "needs": 0, "offers": 0, "allocations": 0, "matches": 0}

    reporters = [
        _mk_person(session, rng, f"5730000001{i:02d}") for i in range(10)
    ]

    demo_needs: list[Need] = []
    for i, target_status in enumerate(CASE_STATES):
        reporter = reporters[i % len(reporters)]
        loc, muni_name = _mk_location(session, rng)
        days_ago = rng.randint(0, 4)
        opened = utcnow() - timedelta(days=days_ago, hours=rng.randint(0, 20))

        conv = Conversation(
            incident_id=incident.id,
            channel_id=channel.id,
            external_thread_key_hmac=phone_hmac(f"5730000001{i % len(reporters):02d}"),
            status=ConversationStatus.OPEN,
            primary_intent=ConversationIntent.NEED_HELP,
            last_message_at=opened,
        )
        session.add(conv)
        session.flush()

        narrative = rng.choice(NARRATIVES)
        session.add(
            Message(
                conversation_id=conv.id,
                provider_message_id=f"wamid.DEMO{i:04d}",
                direction=MessageDirection.INBOUND,
                type=MessageType.TEXT,
                text_enc=encrypt_text(narrative),
                normalized_text_redacted=narrative,
                provider_timestamp=opened,
                received_at=opened,
                delivery_status=MessageDeliveryStatus.DELIVERED,
                processing_status=MessageProcessingStatus.PROCESSED,
            )
        )

        member_count = rng.randint(2, 8)
        household = Household(
            incident_id=incident.id,
            reference_code=f"H-DEMO-{i:03d}",
            head_person_id=reporter.id,
            member_count=member_count,
            minors_count=rng.randint(0, min(3, member_count - 1)),
        )
        session.add(household)
        session.flush()

        case = Case(
            incident_id=incident.id,
            case_code=new_short_code("CU"),
            case_type=DEMO_MARKER,
            household_id=household.id,
            status=target_status,
            opened_at=opened,
        )
        session.add(case)
        session.flush()
        conv.active_case_id = case.id

        # Historia de estados coherente con el estado final
        path = _status_path(target_status)
        for prev, nxt in zip(path, path[1:], strict=False):
            session.add(
                CaseStatusHistory(
                    case_id=case.id,
                    from_status=prev,
                    to_status=nxt,
                    reason_code="DEMO_SEED",
                    changed_at=opened + timedelta(hours=path.index(nxt)),
                )
            )

        report = Report(
            incident_id=incident.id,
            reporter_person_id=reporter.id,
            reporter_role=rng.choice(
                [ReporterRole.SELF, ReporterRole.FAMILY, ReporterRole.COMMUNITY_LEADER]
            ),
            channel_id=channel.id,
            conversation_id=conv.id,
            narrative=narrative,
            location_id=loc.id,
            status=ReportStatus.LINKED,
            submitted_at=opened,
        )
        session.add(report)
        session.flush()
        session.add(
            ReportSubject(
                report_id=report.id,
                case_id=case.id,
                household_id=household.id,
                subject_type=SubjectType.HOUSEHOLD,
            )
        )
        session.add(
            CasePerson(
                case_id=case.id,
                person_id=reporter.id,
                role=CasePersonRole.AFFECTED,
                is_primary=True,
                valid_from=opened,
            )
        )
        session.add(
            Consent(
                person_id=reporter.id,
                incident_id=incident.id,
                purpose=ConsentPurpose.CASE_MANAGEMENT,
                notice_version="aviso-v1-borrador",
                status=ConsentStatus.GRANTED,
                captured_via=ChannelType.WHATSAPP,
                granted_at=opened,
            )
        )

        # 1–3 necesidades por caso
        need_status = {
            CaseStatus.INCOMPLETE: NeedStatus.REPORTED,
            CaseStatus.PENDING_VERIFICATION: NeedStatus.PENDING_VERIFICATION,
            CaseStatus.VERIFIED: NeedStatus.VERIFIED,
            CaseStatus.ACTIVE: NeedStatus.OPEN,
            CaseStatus.PARTIALLY_SERVED: NeedStatus.PARTIALLY_COVERED,
            CaseStatus.SERVED: NeedStatus.DELIVERED_VERIFIED,
        }[target_status]
        for code, qty, _urg in rng.sample(NEED_PLAN, rng.randint(1, 3)):
            cat = catalog.get(code)
            if cat is None:
                continue
            qty_final = Decimal(qty * rng.randint(1, 2))
            # PARTIALLY_COVERED con covered == requested sería una
            # contradicción: la cobertura parcial va entre 30% y 70%.
            if need_status == NeedStatus.DELIVERED_VERIFIED:
                covered = qty_final
            elif need_status == NeedStatus.PARTIALLY_COVERED:
                covered = (qty_final * Decimal(rng.randint(3, 7)) / Decimal(10)).quantize(
                    Decimal("0.1")
                )
            else:
                covered = Decimal(0)
            need = Need(
                case_id=case.id,
                catalog_id=cat.id,
                horizon=cat.default_horizon or NeedHorizon.EMERGENCY,
                status=need_status,
                requested_qty=qty_final,
                confirmed_qty=qty_final if need_status != NeedStatus.REPORTED else None,
                covered_qty=covered,
                unit_code=cat.unit_code,
                description_redacted=f"{cat.name_es} para hogar en {muni_name.title()}",
                # La necesidad nace con el reporte: sin esto la antigüedad de
                # la cola sale en 0 días y la vista de aging no dice nada.
                created_at=opened,
            )
            session.add(need)
            demo_needs.append(need)
            stats["needs"] += 1
        stats["cases"] += 1

    session.flush()

    # ── Ofertas de donantes ────────────────────────────────────────────
    donor_specs = [
        (ResourceType.IN_KIND, "SHELTER.MATTRESS", 30, "Empresa Colchones del Café"),
        (ResourceType.IN_KIND, "FOOD.RATION", 80, "Fundación Manos Unidas"),
        (ResourceType.IN_KIND, "WATER.BOTTLED", 500, "Distribuidora La Fuente"),
        (ResourceType.TRANSPORT, "TRANSPORT.CARGO", 6, "Transportes El Cafetero"),
        (ResourceType.VOLUNTEERING, "SERVICES.DEBRIS", 12, "Voluntarios U. de Caldas"),
    ]
    offers: list[tuple[ResourceOffer, ResourceOfferItem]] = []
    for rtype, code, qty, donor_name in donor_specs:
        donor = _mk_person(session, rng, f"57300000020{len(offers)}")
        cat = catalog.get(code)
        offer = ResourceOffer(
            incident_id=incident.id,
            donor_person_id=donor.id,
            type=rtype,
            status=OfferStatus.AVAILABLE,
            available_from=utcnow() - timedelta(days=1),
            notes_enc=encrypt_json({"donor_display": donor_name}),
        )
        session.add(offer)
        session.flush()
        item = ResourceOfferItem(
            offer_id=offer.id,
            catalog_id=cat.id if cat else None,
            description_redacted=f"{qty} × {cat.name_es if cat else code}",
            quantity=Decimal(qty),
            unit_code=cat.unit_code if cat else "UNIT",
            condition=ItemCondition.NEW,
            reserved_qty=Decimal(0),
            delivered_qty=Decimal(0),
        )
        session.add(item)
        session.flush()
        offers.append((offer, item))
        stats["offers"] += 1

    # ── Matches y una asignación reservada ────────────────────────────
    from app.modules.supply.matching import generate_matches

    mattress_offer, mattress_item = offers[0]
    # Motor real de matching: elegibilidad dura + los dos puntajes de §10.2
    created_matches = []
    for _offer, _item in offers:
        created_matches.extend(generate_matches(session, offer=_offer))
    session.flush()
    stats["matches"] = len(created_matches)

    open_mattress_needs = [
        n for n in demo_needs
        if n.status == NeedStatus.OPEN and n.catalog_id == catalog["SHELTER.MATTRESS"].id
    ]

    if open_mattress_needs:
        need = open_mattress_needs[0]
        first_match = session.execute(
            sa.select(Match).where(Match.need_id == need.id).limit(1)
        ).scalar_one_or_none()
    else:
        first_match = None

    if first_match is not None:
        need = session.get(Need, first_match.need_id)
        qty = min(need.requested_qty or Decimal(3), Decimal(6))
        allocation = Allocation(
            incident_id=incident.id,
            offer_id=mattress_offer.id,
            match_id=first_match.id,
            status=AllocationStatus.RESERVED,
            expires_at=utcnow() + timedelta(hours=48),
        )
        session.add(allocation)
        session.flush()
        session.add(
            AllocationItem(
                allocation_id=allocation.id,
                offer_item_id=mattress_item.id,
                need_id=need.id,
                allocated_qty=qty,
                fulfilled_qty=Decimal(0),
                unit_code="UNIT",
            )
        )
        mattress_item.reserved_qty = qty
        first_match.status = MatchStatus.APPROVED
        stats["allocations"] += 1

    return stats


def seed_public_projections(session) -> dict:
    """Consentimiento de publicación + perfiles públicos + snapshots (§6.3/6.4)."""
    from app.modules.public_impact.models import CtaType, StoryItemType
    from app.modules.public_impact.service import (
        add_story_item,
        build_public_profile,
        rebuild_impact_snapshots,
    )

    incident = _incident(session)
    stats = {"profiles": 0, "story_items": 0, "snapshots": 0}

    publishable = session.execute(
        sa.select(Case)
        .where(
            Case.incident_id == incident.id,
            Case.case_type == DEMO_MARKER,
            Case.status.in_(
                [
                    CaseStatus.VERIFIED,
                    CaseStatus.ACTIVE,
                    CaseStatus.PARTIALLY_SERVED,
                    CaseStatus.SERVED,
                ]
            ),
        )
        .order_by(Case.opened_at)
    ).scalars().all()

    for case in publishable:
        # Consentimiento explícito de publicación para las personas del caso
        for cp in session.execute(
            sa.select(CasePerson).where(CasePerson.case_id == case.id)
        ).scalars().all():
            exists = session.execute(
                sa.select(sa.func.count())
                .select_from(Consent)
                .where(
                    Consent.person_id == cp.person_id,
                    Consent.purpose == ConsentPurpose.PUBLICATION,
                )
            ).scalar()
            if not exists:
                session.add(
                    Consent(
                        person_id=cp.person_id,
                        incident_id=incident.id,
                        purpose=ConsentPurpose.PUBLICATION,
                        notice_version="aviso-v1-borrador",
                        status=ConsentStatus.GRANTED,
                        captured_via=ChannelType.WHATSAPP,
                        granted_at=utcnow(),
                    )
                )
        session.flush()

        needs = session.execute(
            sa.select(Need, NeedCatalog)
            .join(NeedCatalog, NeedCatalog.id == Need.catalog_id)
            .where(Need.case_id == case.id)
        ).all()
        need_labels = [c.name_es.lower() for _, c in needs][:3]
        title = (
            f"Familia necesita {need_labels[0]}"
            if need_labels
            else "Familia afectada requiere apoyo"
        )
        summary = (
            "Hogar afectado por el terremoto del 10 de agosto. "
            + (
                f"Requiere {', '.join(need_labels)}. "
                if need_labels
                else ""
            )
            + "Datos verificados por el equipo en territorio; se publica sin "
            "identificar a las personas."
        )
        profile = build_public_profile(
            session, case, title, summary, publish_now=True
        )
        stats["profiles"] += 1

        add_story_item(
            session,
            profile,
            StoryItemType.NEED,
            "Necesidad registrada y verificada",
            "El equipo confirmó la situación del hogar y publicó la necesidad.",
            cta=CtaType.HELP_THIS_CASE,
            sort_key=1,
        )
        stats["story_items"] += 1
        if case.status in (CaseStatus.PARTIALLY_SERVED, CaseStatus.SERVED):
            add_story_item(
                session,
                profile,
                StoryItemType.DELIVERY,
                "Entrega verificada",
                "Se entregó parte de la ayuda y un validador confirmó la recepción.",
                cta=CtaType.HELP_HIGHEST_NEED,
                sort_key=2,
            )
            stats["story_items"] += 1

    session.flush()
    stats["snapshots"] = rebuild_impact_snapshots(session, incident.id)
    return stats


def main() -> None:
    setup_logging()
    session = get_session_factory()()
    try:
        if _already_seeded(session):
            log_ctx(logger, logging.INFO, "demo cases already present; refreshing public plane")
            stats = {}
        else:
            stats = seed_demo(session)
        stats.update(seed_public_projections(session))
        stats["console_users"] = seed_console_users(session)
        session.commit()
        log_ctx(logger, logging.INFO, "demo data seeded", **stats)
    finally:
        session.close()



# ── Usuarios de la consola (solo demo) ─────────────────────────────────

DEMO_USERS = [
    ("supervisor@colombiaunida.demo", "Demo1234!", RoleCode.SUPERVISOR),
    ("agente@colombiaunida.demo", "Demo1234!", RoleCode.AGENT),
    ("validador@colombiaunida.demo", "Demo1234!", RoleCode.VALIDATOR),
]


def seed_console_users(session) -> int:
    """Crea usuarios de demostración con rol asignado.

    ⚠️ Contraseñas conocidas: exclusivo para el entorno de demostración.
    En piloto/producción los usuarios se crean por invitación con MFA.
    """
    from app.core.passwords import hash_password
    from app.core.security import hmac_index
    from app.modules.identity.models import Role, User, UserRoleAssignment, UserStatus

    created = 0
    for email, password, role_code in DEMO_USERS:
        email_hmac = hmac_index(email)
        existing = session.execute(
            sa.select(User).where(User.email_hmac == email_hmac)
        ).scalar_one_or_none()
        if existing is not None:
            continue
        user = User(
            email_enc=encrypt_text(email),
            email_hmac=email_hmac,
            password_hash=hash_password(password),
            mfa_enrolled=False,  # demo: sin TOTP para no bloquear la muestra
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        session.flush()
        role = session.execute(
            sa.select(Role).where(Role.code == role_code).order_by(Role.version.desc()).limit(1)
        ).scalar_one()
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
        created += 1
    return created

if __name__ == "__main__":
    main()
