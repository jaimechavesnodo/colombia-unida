"""Logística, entrega y evidencia de recepción (tablas 41–45 §7.3).

Sigue las convenciones del módulo identity (EJEMPLAR):
- Enums Python con valores string estables + PgEnum(name="...").
- FKs cross-módulo por nombre de tabla en string.
- Campos [PRIV]/[SENS] cifrados → sufijo _enc, sa.LargeBinary.
"""

import enum

import sqlalchemy as sa
from sqlalchemy.orm import mapped_column

from app.core.db import Base
from app.core.model_base import IdMixin, PgEnum, TimestampMixin, VersionMixin, utcnow

# ── Enums ──────────────────────────────────────────────────────────────


class ShipmentStatus(enum.Enum):
    PLANNED = "PLANNED"
    READY = "READY"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    DELAYED = "DELAYED"
    ARRIVED = "ARRIVED"
    PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"
    DELIVERED = "DELIVERED"
    LOST = "LOST"
    CANCELLED = "CANCELLED"


class CustodyStatus(enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    ACTIVE = "ACTIVE"
    BROKEN = "BROKEN"
    COMPLETE = "COMPLETE"


class StopType(enum.Enum):
    ORIGIN = "ORIGIN"
    HUB = "HUB"
    HANDOFF = "HANDOFF"
    DESTINATION = "DESTINATION"


class StopStatus(enum.Enum):
    PLANNED = "PLANNED"
    ARRIVED = "ARRIVED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class DeliveryStatus(enum.Enum):
    """Estados de entrega §8.2 (enum Postgres "delivery_state").

    El nombre Postgres es "delivery_state" (no "delivery_status") para no
    chocar con el delivery_status de mensajería del módulo intake.
    """

    SCHEDULED = "SCHEDULED"
    ARRIVING = "ARRIVING"
    HANDED_OVER_PENDING_PROOF = "HANDED_OVER_PENDING_PROOF"
    PROOF_SUBMITTED = "PROOF_SUBMITTED"
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ReceiptMethod(enum.Enum):
    OTP = "OTP"
    QR = "QR"
    SIGNATURE = "SIGNATURE"
    PHOTO = "PHOTO"
    AGENT_ATTESTATION = "AGENT_ATTESTATION"
    PARTNER_CONFIRMATION = "PARTNER_CONFIRMATION"


class ReceiptResult(enum.Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    OVERRIDDEN_WITH_REASON = "OVERRIDDEN_WITH_REASON"


# Instancia única: el tipo Postgres "receipt_method" se usa en dos columnas
# (deliveries.verification_method y delivery_receipts.method).
RECEIPT_METHOD = PgEnum(ReceiptMethod, "receipt_method")


# ── Tablas ─────────────────────────────────────────────────────────────


class Shipment(IdMixin, TimestampMixin, VersionMixin, Base):
    """41. Movimiento físico asociado a una asignación."""

    __tablename__ = "shipments"

    allocation_id = mapped_column(sa.Uuid, sa.ForeignKey("allocations.id"), nullable=False)
    shipment_code = mapped_column(sa.String(32), nullable=False, unique=True)
    operator_org_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"))
    status = mapped_column(
        PgEnum(ShipmentStatus, "shipment_status"),
        nullable=False,
        default=ShipmentStatus.PLANNED,
    )
    planned_departure = mapped_column(sa.DateTime(timezone=True))
    planned_arrival = mapped_column(sa.DateTime(timezone=True))
    actual_departure = mapped_column(sa.DateTime(timezone=True))
    actual_arrival = mapped_column(sa.DateTime(timezone=True))
    tracking_ref_enc = mapped_column(sa.LargeBinary)  # [SENS]
    cold_chain_required = mapped_column(sa.Boolean, nullable=False, default=False)
    custody_status = mapped_column(
        PgEnum(CustodyStatus, "custody_status"),
        nullable=False,
        default=CustodyStatus.NOT_STARTED,
    )

    __table_args__ = (sa.Index("ix_shipments_status_planned_arrival", "status", "planned_arrival"),)


class ShipmentStop(IdMixin, TimestampMixin, Base):
    """42. Paradas ordenadas de origen, transferencia y destino."""

    __tablename__ = "shipment_stops"

    shipment_id = mapped_column(sa.Uuid, sa.ForeignKey("shipments.id"), nullable=False)
    sequence_no = mapped_column(sa.Integer, nullable=False)
    stop_type = mapped_column(PgEnum(StopType, "stop_type"), nullable=False)
    location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))  # [SENS]
    contact_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))  # [SENS]
    planned_at = mapped_column(sa.DateTime(timezone=True))
    actual_at = mapped_column(sa.DateTime(timezone=True))
    proof_evidence_id = mapped_column(sa.Uuid, sa.ForeignKey("evidence.id"))  # [PRIV]
    status = mapped_column(
        PgEnum(StopStatus, "stop_status"), nullable=False, default=StopStatus.PLANNED
    )

    __table_args__ = (
        sa.UniqueConstraint("shipment_id", "sequence_no"),
        sa.Index("ix_shipment_stops_location_planned", "location_id", "planned_at"),
    )


class Delivery(IdMixin, TimestampMixin, VersionMixin, Base):
    """43. Acto de entrega a caso/hogar/organización receptora."""

    __tablename__ = "deliveries"

    shipment_id = mapped_column(sa.Uuid, sa.ForeignKey("shipments.id"), nullable=False)
    case_id = mapped_column(sa.Uuid, sa.ForeignKey("cases.id"))
    recipient_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))  # [SENS]
    receiving_org_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"))  # [PRIV]
    delivery_code = mapped_column(sa.String(32), nullable=False, unique=True)
    status = mapped_column(
        PgEnum(DeliveryStatus, "delivery_state"),
        nullable=False,
        default=DeliveryStatus.SCHEDULED,
    )
    location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))  # [SENS]
    delivered_at = mapped_column(sa.DateTime(timezone=True))
    verification_method = mapped_column(RECEIPT_METHOD)
    verified_at = mapped_column(sa.DateTime(timezone=True))
    dispute_reason = mapped_column(sa.Text)  # [PRIV]

    __table_args__ = (
        sa.Index("ix_deliveries_case_status", "case_id", "status"),
        sa.Index("ix_deliveries_delivered_status", "delivered_at", "status"),
    )


class DeliveryItem(IdMixin, TimestampMixin, Base):
    """44. Cantidad efectivamente entregada por línea asignada."""

    __tablename__ = "delivery_items"

    delivery_id = mapped_column(sa.Uuid, sa.ForeignKey("deliveries.id"), nullable=False)
    allocation_item_id = mapped_column(
        sa.Uuid, sa.ForeignKey("allocation_items.id"), nullable=False
    )
    delivered_qty = mapped_column(sa.Numeric, nullable=False)
    accepted_qty = mapped_column(sa.Numeric)
    rejected_qty = mapped_column(sa.Numeric)
    condition_note = mapped_column(sa.Text)  # [PRIV]
    serial_or_lot_enc = mapped_column(sa.LargeBinary)  # [SENS]

    __table_args__ = (
        sa.UniqueConstraint("delivery_id", "allocation_item_id"),
        sa.CheckConstraint(
            "accepted_qty IS NULL OR rejected_qty IS NULL "
            "OR delivered_qty >= accepted_qty + rejected_qty",
            name="ck_delivery_items_qty_coherent",
        ),
    )


class DeliveryReceipt(IdMixin, TimestampMixin, Base):
    """45. Confirmación del receptor mediante OTP/código, firma, agente o aliado."""

    __tablename__ = "delivery_receipts"

    delivery_id = mapped_column(sa.Uuid, sa.ForeignKey("deliveries.id"), nullable=False)
    method = mapped_column(RECEIPT_METHOD, nullable=False)
    receiver_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))  # [SENS]
    otp_hash = mapped_column(sa.LargeBinary)  # [SENS] OTP nunca en claro
    signature_object_uri = mapped_column(sa.Text)  # [SENS]
    witness_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    result = mapped_column(PgEnum(ReceiptResult, "receipt_result"))
    captured_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    device_context_enc = mapped_column(sa.LargeBinary)  # [SENS] jsonb cifrado

    __table_args__ = (
        sa.Index("ix_delivery_receipts_delivery_captured", "delivery_id", "captured_at"),
        # Un solo OTP activo (emitido y aún sin resultado) por delivery (§7.3-45).
        sa.Index(
            "uq_delivery_receipts_active_otp",
            "delivery_id",
            unique=True,
            postgresql_where=sa.text("otp_hash IS NOT NULL AND result IS NULL"),
        ),
    )
