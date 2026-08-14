"""Ofertas, matching y asignación (tablas 32–40 §7.3) + referencias de pago (46).

Sigue las convenciones del módulo identity (EJEMPLAR):
- Tabla y columnas en snake_case, nombres exactos del diccionario.
- Enums Python con valores string estables + PgEnum(name="<entidad>_<campo>").
- FKs por nombre de tabla: sa.ForeignKey("resource_offers.id").
- Campos [PRIV]/[SENS] cifrados → sufijo _enc, sa.LargeBinary.
- Geometrías → geoalchemy2.Geometry (SRID 4326).
- JSONB → sqlalchemy.dialects.postgresql.JSONB.
- Optimistic locking (VersionMixin) en agregados con reservas: offers y allocations.
"""

import enum

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import mapped_column

from app.core.db import Base
from app.core.model_base import IdMixin, PgEnum, TimestampMixin, VersionMixin, utcnow
from app.modules.cases.models import TrustReviewState

# ── Enums ──────────────────────────────────────────────────────────────


class ResourceType(enum.Enum):
    MONEY = "MONEY"
    IN_KIND = "IN_KIND"
    SERVICE = "SERVICE"
    TRANSPORT = "TRANSPORT"
    VOLUNTEERING = "VOLUNTEERING"


class OfferStatus(enum.Enum):
    """OFFER §8.2: camino feliz DRAFT → … → FULLY_ALLOCATED; excepciones al final."""

    DRAFT = "DRAFT"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    AVAILABLE = "AVAILABLE"
    PARTIALLY_ALLOCATED = "PARTIALLY_ALLOCATED"
    FULLY_ALLOCATED = "FULLY_ALLOCATED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"
    BLOCKED = "BLOCKED"


class AllocationPreference(enum.Enum):
    SPECIFIC_CASE = "SPECIFIC_CASE"
    HIGHEST_NEED = "HIGHEST_NEED"
    OPERATOR_DECIDES = "OPERATOR_DECIDES"


class ItemCondition(enum.Enum):
    NEW = "NEW"
    GOOD = "GOOD"
    USED = "USED"
    REFURBISHED = "REFURBISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MatchStatus(enum.Enum):
    """MATCH §8.2."""

    PROPOSED = "PROPOSED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class AllocationStatus(enum.Enum):
    """ALLOCATION §8.2."""

    DRAFT = "DRAFT"
    RESERVED = "RESERVED"
    DONOR_CONFIRMED = "DONOR_CONFIRMED"
    ACCEPTED_BY_OPERATOR = "ACCEPTED_BY_OPERATOR"
    READY_FOR_FULFILLMENT = "READY_FOR_FULFILLMENT"
    IN_FULFILLMENT = "IN_FULFILLMENT"
    FULFILLED = "FULFILLED"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    DISPUTED = "DISPUTED"


class CheckStatus(enum.Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class PaymentRefStatus(enum.Enum):
    INITIATED = "INITIATED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"


# ── Tablas ─────────────────────────────────────────────────────────────


class ResourceOffer(IdMixin, TimestampMixin, VersionMixin, Base):
    """32. Oferta común de dinero, especie, servicio, transporte o voluntariado."""

    __tablename__ = "resource_offers"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"), nullable=False)
    donor_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    donor_org_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"))
    type = mapped_column(PgEnum(ResourceType, "resource_type"), nullable=False)
    status = mapped_column(
        PgEnum(OfferStatus, "offer_status"), nullable=False, default=OfferStatus.DRAFT
    )
    available_from = mapped_column(sa.DateTime(timezone=True))
    available_to = mapped_column(sa.DateTime(timezone=True))
    origin_location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))
    destination_constraints = mapped_column(JSONB)
    allocation_preference = mapped_column(
        PgEnum(AllocationPreference, "allocation_preference"),
        nullable=False,
        default=AllocationPreference.OPERATOR_DECIDES,
    )
    notes_enc = mapped_column(sa.LargeBinary)
    trust_review_state = mapped_column(
        PgEnum(TrustReviewState, "trust_review_state"),
        nullable=False,
        default=TrustReviewState.PENDING,
    )

    __table_args__ = (
        sa.Index(
            "ix_resource_offers_incident_type_status",
            "incident_id",
            "type",
            "status",
            "available_from",
        ),
    )


class ResourceOfferItem(IdMixin, TimestampMixin, Base):
    """33. Líneas cuantificables de una oferta, ligadas al catálogo de necesidades."""

    __tablename__ = "resource_offer_items"

    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"), nullable=False)
    catalog_id = mapped_column(sa.Uuid)  # FK lógica a need_catalog (módulo cases)
    description_redacted = mapped_column(sa.Text)
    quantity = mapped_column(sa.Numeric, nullable=False)
    unit_code = mapped_column(sa.Text)
    condition = mapped_column(
        PgEnum(ItemCondition, "item_condition"),
        nullable=False,
        default=ItemCondition.NOT_APPLICABLE,
    )
    expiry_date = mapped_column(sa.Date)
    reserved_qty = mapped_column(sa.Numeric)
    delivered_qty = mapped_column(sa.Numeric)
    attributes = mapped_column(JSONB)

    __table_args__ = (
        sa.Index("ix_resource_offer_items_offer_catalog", "offer_id", "catalog_id"),
        sa.CheckConstraint(
            "coalesce(reserved_qty, 0) + coalesce(delivered_qty, 0) <= quantity",
            name="ck_offer_items_qty",
        ),
    )


class MoneyOfferDetail(TimestampMixin, Base):
    """34. Detalle monetario sin custodiar fondos en la plataforma (1:1 con oferta)."""

    __tablename__ = "money_offer_details"

    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"), primary_key=True)
    currency = mapped_column(sa.CHAR(3), nullable=False, default="COP")
    pledged_amount = mapped_column(sa.Numeric)
    max_case_count = mapped_column(sa.Integer)
    designated_case_id = mapped_column(sa.Uuid)  # FK lógica a cases (módulo cases)
    funding_partner_org_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"))
    payment_route = mapped_column(sa.Text)
    settlement_required = mapped_column(sa.Boolean, nullable=False, default=False)

    __table_args__ = (
        sa.CheckConstraint("pledged_amount > 0", name="ck_money_offer_amount_positive"),
    )


class ServiceOfferDetail(TimestampMixin, Base):
    """35. Capacidad profesional o empresarial, requisitos y unidad de servicio."""

    __tablename__ = "service_offer_details"

    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"), primary_key=True)
    service_category = mapped_column(sa.Text, nullable=False, index=True)
    capacity = mapped_column(sa.Numeric)
    unit_code = mapped_column(sa.Text)
    credentials_required = mapped_column(sa.Boolean, nullable=False, default=False)
    credentials_evidence_id = mapped_column(sa.Uuid)  # FK lógica a evidence (módulo fulfillment)
    service_area = mapped_column(Geometry(srid=4326, spatial_index=True))
    schedule = mapped_column(JSONB)


class TransportOfferDetail(TimestampMixin, Base):
    """36. Vehículo, capacidad, ruta y restricciones logísticas."""

    __tablename__ = "transport_offer_details"

    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"), primary_key=True)
    vehicle_type = mapped_column(sa.Text)
    capacity_weight_kg = mapped_column(sa.Numeric)
    capacity_volume_m3 = mapped_column(sa.Numeric)
    origin_location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))
    service_area = mapped_column(Geometry(srid=4326, spatial_index=True))
    cold_chain = mapped_column(sa.Boolean, nullable=False, default=False)
    hazardous_allowed = mapped_column(sa.Boolean, nullable=False, default=False)
    driver_requirements = mapped_column(JSONB)

    __table_args__ = (
        sa.Index("ix_transport_offer_flags", "cold_chain", "hazardous_allowed"),
    )


class VolunteerOfferDetail(TimestampMixin, Base):
    """37. Disponibilidad, habilidades y restricciones de una persona voluntaria."""

    __tablename__ = "volunteer_offer_details"

    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"), primary_key=True)
    skills = mapped_column(ARRAY(sa.Text))
    availability = mapped_column(JSONB)
    max_hours = mapped_column(sa.Numeric)
    service_area = mapped_column(Geometry(srid=4326, spatial_index=True))
    background_check_status = mapped_column(
        PgEnum(CheckStatus, "check_status"),
        nullable=False,
        default=CheckStatus.NOT_REQUIRED,
    )
    safeguarding_training_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_volunteer_offer_skills", "skills", postgresql_using="gin"),
    )


class Match(IdMixin, TimestampMixin, Base):
    """38. Candidato necesidad↔recurso, con restricciones, puntajes y explicación."""

    __tablename__ = "matches"

    need_id = mapped_column(sa.Uuid, nullable=False)  # FK lógica a needs (módulo cases)
    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"), nullable=False)
    offer_item_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offer_items.id"))
    algorithm_version = mapped_column(sa.Text, nullable=False)
    eligibility_passed = mapped_column(sa.Boolean)
    rejection_reasons = mapped_column(ARRAY(sa.Text))
    humanitarian_priority_score = mapped_column(sa.Numeric)
    feasibility_score = mapped_column(sa.Numeric)
    final_rank = mapped_column(sa.Numeric)
    explanation_json = mapped_column(JSONB)
    status = mapped_column(
        PgEnum(MatchStatus, "match_status"), nullable=False, default=MatchStatus.PROPOSED
    )
    generated_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        sa.Index("ix_matches_need_status_rank", "need_id", "status", "final_rank"),
        sa.Index("ix_matches_offer_status_rank", "offer_id", "status", "final_rank"),
        # "UNIQUE por versión/pareja activa": único parcial sobre estados vivos.
        sa.Index(
            "uq_matches_active_pair",
            "need_id",
            "offer_id",
            "offer_item_id",
            "algorithm_version",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('PROPOSED', 'REVIEW_REQUIRED', 'APPROVED')"
            ),
        ),
    )


class Allocation(IdMixin, TimestampMixin, VersionMixin, Base):
    """39. Compromiso aprobado que reserva una oferta para casos/necesidades."""

    __tablename__ = "allocations"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"), nullable=False)
    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"), nullable=False)
    match_id = mapped_column(sa.Uuid, sa.ForeignKey("matches.id"))
    status = mapped_column(
        PgEnum(AllocationStatus, "allocation_status"),
        nullable=False,
        default=AllocationStatus.DRAFT,
    )
    approved_by = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    donor_confirmed_at = mapped_column(sa.DateTime(timezone=True))
    expires_at = mapped_column(sa.DateTime(timezone=True))
    terms_version = mapped_column(sa.Text)
    public_acknowledgement_pref = mapped_column(sa.Text)

    __table_args__ = (
        sa.Index("ix_allocations_offer_status", "offer_id", "status"),
        sa.Index("ix_allocations_expires_status", "expires_at", "status"),
    )


class AllocationItem(IdMixin, TimestampMixin, Base):
    """40. Cantidad concreta asignada de un ítem a una necesidad."""

    __tablename__ = "allocation_items"

    allocation_id = mapped_column(sa.Uuid, sa.ForeignKey("allocations.id"), nullable=False)
    offer_item_id = mapped_column(
        sa.Uuid, sa.ForeignKey("resource_offer_items.id"), nullable=False
    )
    need_id = mapped_column(sa.Uuid, nullable=False)  # FK lógica a needs (módulo cases)
    allocated_qty = mapped_column(sa.Numeric, nullable=False)
    fulfilled_qty = mapped_column(sa.Numeric)
    unit_code = mapped_column(sa.Text)
    unit_value_ref = mapped_column(sa.Numeric)
    currency = mapped_column(sa.CHAR(3))

    __table_args__ = (
        sa.UniqueConstraint("allocation_id", "offer_item_id", "need_id"),
        sa.CheckConstraint(
            "fulfilled_qty <= allocated_qty", name="ck_allocation_items_fulfilled"
        ),
    )


class ExternalPaymentRef(IdMixin, TimestampMixin, Base):
    """46. Referencia de pago/settlement operado por tercero; la plataforma no custodia."""

    __tablename__ = "external_payment_refs"

    offer_id = mapped_column(sa.Uuid, sa.ForeignKey("resource_offers.id"))
    allocation_id = mapped_column(sa.Uuid, sa.ForeignKey("allocations.id"))
    partner_org_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"))
    external_reference_enc = mapped_column(sa.LargeBinary)
    amount = mapped_column(sa.Numeric)
    currency = mapped_column(sa.CHAR(3))
    status = mapped_column(
        PgEnum(PaymentRefStatus, "payment_ref_status"),
        nullable=False,
        default=PaymentRefStatus.INITIATED,
    )
    settled_at = mapped_column(sa.DateTime(timezone=True))
    reconciliation_hash = mapped_column(sa.LargeBinary)

    __table_args__ = (
        sa.UniqueConstraint("partner_org_id", "reconciliation_hash"),
        sa.Index("ix_external_payment_refs_status_settled", "status", "settled_at"),
    )
