"""Confianza, riesgo, auditoría y gobierno de datos (tablas 49, 50, 52, 60, 61 §7.3).

Sigue las convenciones del módulo identity (EJEMPLAR):
- Enums Python con valores string estables + PgEnum(name="...").
- Referencias polimórficas (subject_id, entity_id) → sa.Uuid sin FK.
- Hashes e índices HMAC → sa.LargeBinary.
"""

import enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import mapped_column

from app.core.db import Base
from app.core.model_base import IdMixin, PgEnum, TimestampMixin, utcnow

# ── Enums ──────────────────────────────────────────────────────────────


class TrustSubjectType(enum.Enum):
    REPORTER = "REPORTER"
    CASE = "CASE"
    OFFER = "OFFER"
    DELIVERY = "DELIVERY"


class TrustBand(enum.Enum):
    LOW_EVIDENCE = "LOW_EVIDENCE"
    STANDARD_REVIEW = "STANDARD_REVIEW"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    ESCALATED = "ESCALATED"


class RiskSeverity(enum.Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskStatus(enum.Enum):
    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    MITIGATED = "MITIGATED"
    CONFIRMED_ISSUE = "CONFIRMED_ISSUE"
    CLOSED = "CLOSED"


class ActorType(enum.Enum):
    USER = "USER"
    PERSON = "PERSON"
    SYSTEM = "SYSTEM"
    PARTNER = "PARTNER"


class AccessAction(enum.Enum):
    VIEW = "VIEW"
    SEARCH = "SEARCH"
    DOWNLOAD = "DOWNLOAD"
    EXPORT = "EXPORT"
    DECRYPT = "DECRYPT"
    BREAK_GLASS = "BREAK_GLASS"


class RetentionJobStatus(enum.Enum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DataClass(enum.Enum):
    """Clasificación de datos §6: PUB, INT, PRIV, SENS."""

    PUB = "PUB"
    INT = "INT"
    PRIV = "PRIV"
    SENS = "SENS"


# ── Tablas ─────────────────────────────────────────────────────────────


class TrustAssessment(IdMixin, TimestampMixin, Base):
    """49. Evaluación versionada de confianza para dirigir verificación."""

    __tablename__ = "trust_assessments"

    subject_type = mapped_column(
        PgEnum(TrustSubjectType, "trust_subject_type"), nullable=False
    )
    # FK lógica polimórfica a reporter/case/offer/delivery según subject_type.
    subject_id = mapped_column(sa.Uuid, nullable=False)
    model_version = mapped_column(sa.Text, nullable=False)
    score = mapped_column(sa.Numeric(5, 2))
    band = mapped_column(PgEnum(TrustBand, "trust_band"), nullable=False)
    component_scores = mapped_column(JSONB)
    missing_evidence = mapped_column(ARRAY(sa.Text))
    reviewer_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    effective_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    supersedes_id = mapped_column(sa.Uuid, sa.ForeignKey("trust_assessments.id"))

    __table_args__ = (
        # Consulta típica: última evaluación vigente (effective_at DESC).
        sa.Index("ix_trust_assessments_subject", "subject_type", "subject_id", "effective_at"),
    )


class RiskSignal(IdMixin, TimestampMixin, Base):
    """50. Señal explicable y accionable; nunca equivale por sí sola a fraude."""

    __tablename__ = "risk_signals"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"))
    # subject_type es text (no enum) en el diccionario: catálogo abierto de entidades.
    subject_type = mapped_column(sa.Text, nullable=False)
    subject_id = mapped_column(sa.Uuid, nullable=False)  # FK lógica polimórfica
    rule_code = mapped_column(sa.Text, nullable=False)
    rule_version = mapped_column(sa.Text, nullable=False)
    severity = mapped_column(PgEnum(RiskSeverity, "risk_severity"), nullable=False)
    status = mapped_column(
        PgEnum(RiskStatus, "risk_status"), nullable=False, default=RiskStatus.OPEN
    )
    evidence_json_enc = mapped_column(sa.LargeBinary)  # [SENS] jsonb cifrado
    detected_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    resolved_by = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    resolution = mapped_column(sa.Text)  # [PRIV]
    # Huella determinística de la señal para el UNIQUE de dedupe (§7.3-50).
    signal_fingerprint = mapped_column(sa.LargeBinary, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "rule_code", "rule_version", "subject_type", "subject_id", "signal_fingerprint"
        ),
        sa.Index(
            "ix_risk_signals_incident_status",
            "incident_id",
            "status",
            "severity",
            "detected_at",
        ),
    )


class AuditEvent(IdMixin, Base):
    """52. Bitácora append-only y encadenada de acciones, cambios y accesos.

    Sin TimestampMixin: occurred_at es el tiempo canónico y no existe
    updated_at porque UPDATE/DELETE están denegados (append-only, hash chain).
    """

    __tablename__ = "audit_events"

    occurred_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    actor_type = mapped_column(PgEnum(ActorType, "actor_type"), nullable=False)
    actor_id = mapped_column(sa.Uuid)  # FK lógica según actor_type
    action = mapped_column(sa.Text, nullable=False)
    entity_type = mapped_column(sa.Text, nullable=False)
    entity_id = mapped_column(sa.Uuid)  # FK lógica a la entidad afectada
    before_redacted = mapped_column(JSONB)  # [PRIV] sin secretos ni P3 en claro
    after_redacted = mapped_column(JSONB)  # [PRIV]
    correlation_id = mapped_column(sa.Uuid)
    request_id = mapped_column(sa.Text)
    previous_hash = mapped_column(sa.LargeBinary)  # [SENS] eslabón anterior de la cadena
    event_hash = mapped_column(sa.LargeBinary, nullable=False, unique=True)  # [SENS]
    anchor_object_uri = mapped_column(sa.Text)  # [SENS]

    __table_args__ = (
        sa.Index("ix_audit_events_entity", "entity_type", "entity_id", "occurred_at"),
    )


class AccessEvent(IdMixin, TimestampMixin, Base):
    """60. Registro de lectura/descarga/exportación de datos protegidos."""

    __tablename__ = "access_events"

    actor_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    purpose_code = mapped_column(sa.Text)
    entity_type = mapped_column(sa.Text, nullable=False)
    entity_id = mapped_column(sa.Uuid)  # FK lógica a la entidad consultada
    field_classification = mapped_column(PgEnum(DataClass, "data_class"))
    action = mapped_column(PgEnum(AccessAction, "access_action"), nullable=False)
    occurred_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    ip_hash = mapped_column(sa.LargeBinary)  # [SENS]
    device_hash = mapped_column(sa.LargeBinary)  # [SENS]
    break_glass_reason = mapped_column(sa.Text)  # [PRIV]

    __table_args__ = (
        sa.Index("ix_access_events_actor_occurred", "actor_user_id", "occurred_at"),
        sa.Index("ix_access_events_entity", "entity_type", "entity_id"),
    )


class RetentionJob(IdMixin, TimestampMixin, Base):
    """61. Ejecuciones controladas de retención, anonimización y eliminación."""

    __tablename__ = "retention_jobs"

    policy_code = mapped_column(sa.Text, nullable=False)
    policy_version = mapped_column(sa.Text, nullable=False)
    entity_type = mapped_column(sa.Text, nullable=False)
    cutoff_at = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status = mapped_column(
        PgEnum(RetentionJobStatus, "retention_job_status"),
        nullable=False,
        default=RetentionJobStatus.PLANNED,
    )
    dry_run = mapped_column(sa.Boolean, nullable=False, default=True)
    affected_count = mapped_column(sa.BigInteger)
    error_count = mapped_column(sa.BigInteger)
    manifest_uri = mapped_column(sa.Text)  # [SENS] manifiesto inmutable
    approved_by = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    started_at = mapped_column(sa.DateTime(timezone=True))
    completed_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_retention_jobs_policy_status", "policy_code", "status", "cutoff_at"),
    )
