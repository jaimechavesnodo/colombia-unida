"""Reportes, casos y necesidades + evidencia y duplicados (tablas 13–21, 47–48, 51 §7.3).

Sigue las convenciones del ejemplar `app.modules.identity.models`:
- Tabla y columnas en snake_case, nombres exactos del diccionario.
- Enums Python con valores string estables + PgEnum(name="<entidad>_<campo>").
- FKs por nombre de tabla: sa.ForeignKey("cases.id"). FKs polimórficas → sa.Uuid
  con comentario `# FK lógica`.
- Campos [PRIV]/[SENS] cifrados → sufijo _enc, sa.LargeBinary.
- JSONB → sqlalchemy.dialects.postgresql.JSONB.
- `version bigint` (VersionMixin) en agregados mutables: cases y needs.
"""

import enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column

from app.core.db import Base
from app.core.model_base import IdMixin, PgEnum, TimestampMixin, VersionMixin, utcnow

# ── Enums ──────────────────────────────────────────────────────────────


class ReportStatus(enum.Enum):
    """Máquina de estados REPORT (§8.2)."""

    DRAFT = "DRAFT"
    COLLECTING = "COLLECTING"
    SUBMITTED_INCOMPLETE = "SUBMITTED_INCOMPLETE"
    SUBMITTED = "SUBMITTED"
    LINKED = "LINKED"
    DUPLICATE = "DUPLICATE"
    WITHDRAWN = "WITHDRAWN"
    REJECTED = "REJECTED"


class ReporterRole(enum.Enum):
    SELF = "SELF"
    FAMILY = "FAMILY"
    NEIGHBOR = "NEIGHBOR"
    COMMUNITY_LEADER = "COMMUNITY_LEADER"
    OFFICIAL = "OFFICIAL"
    THIRD_PARTY = "THIRD_PARTY"


class SubjectType(enum.Enum):
    PERSON = "PERSON"
    HOUSEHOLD = "HOUSEHOLD"
    COMMUNITY_ASSET = "COMMUNITY_ASSET"
    BUSINESS = "BUSINESS"


class CaseStatus(enum.Enum):
    """Máquina de estados CASE (§8.2)."""

    DRAFT = "DRAFT"
    INCOMPLETE = "INCOMPLETE"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    PARTIALLY_SERVED = "PARTIALLY_SERVED"
    SERVED = "SERVED"
    CLOSED = "CLOSED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    SUSPICIOUS = "SUSPICIOUS"


class PriorityBand(enum.Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class TrustReviewState(enum.Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    CLEARED = "CLEARED"
    ESCALATED = "ESCALATED"


class CasePersonRole(enum.Enum):
    AFFECTED = "AFFECTED"
    REPORTER = "REPORTER"
    CAREGIVER = "CAREGIVER"
    BENEFICIARY_CONTACT = "BENEFICIARY_CONTACT"
    COMMUNITY_LEADER = "COMMUNITY_LEADER"
    RECEIVER = "RECEIVER"
    WITNESS = "WITNESS"


class NeedHorizon(enum.Enum):
    EMERGENCY = "EMERGENCY"
    RECOVERY = "RECOVERY"
    RECONSTRUCTION = "RECONSTRUCTION"


class NeedStatus(enum.Enum):
    """Máquina de estados NEED (§8.2)."""

    REPORTED = "REPORTED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    PUBLICABLE = "PUBLICABLE"
    OPEN = "OPEN"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"
    COVERED = "COVERED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED_PENDING_VERIFY = "DELIVERED_PENDING_VERIFY"
    DELIVERED_VERIFIED = "DELIVERED_VERIFIED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    CANCELLED = "CANCELLED"


class UrgencyLevel(enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ValidationType(enum.Enum):
    DOCUMENT = "DOCUMENT"
    PHONE = "PHONE"
    COMMUNITY = "COMMUNITY"
    REMOTE_MEDIA = "REMOTE_MEDIA"
    FIELD_VISIT = "FIELD_VISIT"
    TECHNICAL = "TECHNICAL"


class ValidationOutcome(enum.Enum):
    PASS = "PASS"  # noqa: S105 — resultado de validación, no una contraseña
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXPIRED = "EXPIRED"


class EvidenceType(enum.Enum):
    BEFORE_PHOTO = "BEFORE_PHOTO"
    DAMAGE_PHOTO = "DAMAGE_PHOTO"
    DOCUMENT = "DOCUMENT"
    GPS = "GPS"
    VALIDATION_NOTE = "VALIDATION_NOTE"
    PICKUP_PROOF = "PICKUP_PROOF"
    DELIVERY_PHOTO = "DELIVERY_PHOTO"
    RECEIPT = "RECEIPT"
    AFTER_PHOTO = "AFTER_PHOTO"
    SIGNATURE = "SIGNATURE"


class EvidenceStatus(enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    TAMPER_ALERT = "TAMPER_ALERT"


class EvidenceVisibility(enum.Enum):
    PROTECTED = "PROTECTED"
    AUDITOR_ONLY = "AUDITOR_ONLY"
    PUBLIC_DERIVATIVE = "PUBLIC_DERIVATIVE"


class EvidenceEntityType(enum.Enum):
    CASE = "CASE"
    NEED = "NEED"
    VALIDATION = "VALIDATION"
    OFFER = "OFFER"
    SHIPMENT = "SHIPMENT"
    DELIVERY = "DELIVERY"
    PERSON_CREDENTIAL = "PERSON_CREDENTIAL"


class EvidenceRelation(enum.Enum):
    SUPPORTS = "SUPPORTS"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    RECEIPT = "RECEIPT"
    IDENTITY = "IDENTITY"
    TECHNICAL = "TECHNICAL"


class DuplicateEntityType(enum.Enum):
    REPORT = "REPORT"
    CASE = "CASE"
    PERSON = "PERSON"
    MEDIA = "MEDIA"


class DuplicateStatus(enum.Enum):
    PROPOSED = "PROPOSED"
    REVIEWING = "REVIEWING"
    CONFIRMED = "CONFIRMED"
    NOT_DUPLICATE = "NOT_DUPLICATE"
    MERGED = "MERGED"
    DEFERRED = "DEFERRED"


# ── Tablas ─────────────────────────────────────────────────────────────


class Report(IdMixin, TimestampMixin, Base):
    """13. Declaración recibida; una persona puede emitir muchas."""

    __tablename__ = "reports"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"), nullable=False)
    reporter_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    reporter_role = mapped_column(PgEnum(ReporterRole, "reporter_role"), nullable=False)
    channel_id = mapped_column(sa.Uuid, sa.ForeignKey("channels.id"))
    conversation_id = mapped_column(sa.Uuid, sa.ForeignKey("conversations.id"))
    # [SENS] Puede contener datos no solicitados; requiere DLP. Trigram solo
    # sobre derivado redactado (migración posterior), nunca sobre el original.
    narrative = mapped_column(sa.Text)
    location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))
    status = mapped_column(
        PgEnum(ReportStatus, "report_status"), nullable=False, default=ReportStatus.DRAFT
    )
    completeness_score = mapped_column(sa.Numeric(5, 2))
    submitted_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_reports_incident_status_submitted", "incident_id", "status", "submitted_at"),
    )


class ReportSubject(IdMixin, TimestampMixin, Base):
    """14. Vincula un reporte con casos, personas u hogares reportados."""

    __tablename__ = "report_subjects"

    report_id = mapped_column(sa.Uuid, sa.ForeignKey("reports.id"), nullable=False)
    case_id = mapped_column(sa.Uuid, sa.ForeignKey("cases.id"))
    person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    household_id = mapped_column(sa.Uuid, sa.ForeignKey("households.id"))
    subject_type = mapped_column(PgEnum(SubjectType, "subject_type"), nullable=False)
    relation_to_reporter = mapped_column(sa.Text)
    confidence = mapped_column(sa.Numeric)

    __table_args__ = (
        # UNIQUE(report_id, subject_type, coalesce ids) del diccionario.
        sa.Index(
            "uq_report_subjects_subject",
            "report_id",
            "subject_type",
            sa.text("coalesce(case_id, person_id, household_id)"),
            unique=True,
        ),
        sa.Index("ix_report_subjects_case", "case_id"),
    )


class Case(IdMixin, TimestampMixin, VersionMixin, Base):
    """15. Unidad operativa central que consolida reportes y necesidades."""

    __tablename__ = "cases"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"), nullable=False)
    case_code = mapped_column(sa.String(32), nullable=False)
    # El diccionario declara enum case_type pero no define sus valores;
    # se modela como texto hasta que la taxonomía quede catalogada.
    case_type = mapped_column(sa.Text)
    household_id = mapped_column(sa.Uuid, sa.ForeignKey("households.id"))
    primary_location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))
    status = mapped_column(
        PgEnum(CaseStatus, "case_status"), nullable=False, default=CaseStatus.DRAFT
    )
    priority_band = mapped_column(PgEnum(PriorityBand, "priority_band"))
    completeness_score = mapped_column(sa.Numeric)
    trust_review_state = mapped_column(
        PgEnum(TrustReviewState, "trust_review_state"),
        nullable=False,
        default=TrustReviewState.NONE,
    )
    opened_at = mapped_column(sa.DateTime(timezone=True))
    closed_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint("incident_id", "case_code"),
        sa.Index("ix_cases_incident_status_priority", "incident_id", "status", "priority_band"),
        # GIN para búsqueda interna controlada: requiere columna tsvector
        # derivada; se agrega en migración posterior.
    )


class CasePerson(IdMixin, TimestampMixin, Base):
    """16. Participación de personas en un caso (reportante != afectado)."""

    __tablename__ = "case_persons"

    case_id = mapped_column(sa.Uuid, sa.ForeignKey("cases.id"), nullable=False)
    person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"), nullable=False)
    role = mapped_column(PgEnum(CasePersonRole, "case_person_role"), nullable=False)
    is_primary = mapped_column(sa.Boolean, nullable=False, default=False)
    valid_from = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    valid_to = mapped_column(sa.DateTime(timezone=True))
    source_report_id = mapped_column(sa.Uuid, sa.ForeignKey("reports.id"))

    __table_args__ = (
        sa.UniqueConstraint("case_id", "person_id", "role", "valid_from"),
        sa.Index("ix_case_persons_person_role", "person_id", "role"),
    )


class CaseStatusHistory(IdMixin, Base):
    """17. Historial append-only de cambios de estado de caso.

    Append-only: la revocación de UPDATE/DELETE al rol de aplicación se
    aplica por DDL en migración posterior.
    """

    __tablename__ = "case_status_history"

    case_id = mapped_column(sa.Uuid, sa.ForeignKey("cases.id"), nullable=False)
    from_status = mapped_column(PgEnum(CaseStatus, "case_status"))
    to_status = mapped_column(PgEnum(CaseStatus, "case_status"), nullable=False)
    reason_code = mapped_column(sa.Text)
    note_redacted = mapped_column(sa.Text)  # nunca narrativa sensible sin redacción
    changed_by_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    changed_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    correlation_id = mapped_column(sa.Uuid)

    __table_args__ = (sa.Index("ix_case_status_history_case_changed", "case_id", "changed_at"),)


class NeedCatalog(IdMixin, TimestampMixin, Base):
    """18. Taxonomía versionada de necesidades comparables."""

    __tablename__ = "need_catalog"

    parent_id = mapped_column(sa.Uuid, sa.ForeignKey("need_catalog.id"))
    code = mapped_column(sa.String(64), nullable=False)
    name_es = mapped_column(sa.Text, nullable=False)
    unit_code = mapped_column(sa.String(16))
    default_horizon = mapped_column(PgEnum(NeedHorizon, "need_horizon"))
    active = mapped_column(sa.Boolean, nullable=False, default=True)
    version = mapped_column(sa.Integer, nullable=False, default=1)
    attributes_schema = mapped_column(JSONB)

    __table_args__ = (
        sa.UniqueConstraint("code", "version"),
        sa.Index("ix_need_catalog_parent_active", "parent_id", "active"),
    )


class Need(IdMixin, TimestampMixin, VersionMixin, Base):
    """19. Demanda concreta, cuantificable y gestionable dentro de un caso."""

    __tablename__ = "needs"

    case_id = mapped_column(sa.Uuid, sa.ForeignKey("cases.id"), nullable=False)
    catalog_id = mapped_column(sa.Uuid, sa.ForeignKey("need_catalog.id"), nullable=False)
    horizon = mapped_column(PgEnum(NeedHorizon, "need_horizon"), nullable=False)
    status = mapped_column(
        PgEnum(NeedStatus, "need_status"), nullable=False, default=NeedStatus.REPORTED
    )
    requested_qty = mapped_column(sa.Numeric)
    confirmed_qty = mapped_column(sa.Numeric)
    covered_qty = mapped_column(sa.Numeric)
    unit_code = mapped_column(sa.Text)
    urgency = mapped_column(PgEnum(UrgencyLevel, "urgency_level"))
    needed_by = mapped_column(sa.Date)
    priority_score = mapped_column(sa.Numeric)
    description_redacted = mapped_column(sa.Text)  # derivado publicable, no el original
    attributes_enc = mapped_column(sa.LargeBinary)  # [SENS] jsonb cifrado
    __table_args__ = (
        sa.CheckConstraint(
            "covered_qty IS NULL OR confirmed_qty IS NULL OR covered_qty <= confirmed_qty",
            name="ck_needs_covered_le_confirmed",
        ),
        sa.Index("ix_needs_case_status", "case_id", "status"),
        sa.Index("ix_needs_catalog_horizon_status", "catalog_id", "horizon", "status"),
    )


class NeedStatusHistory(IdMixin, Base):
    """20. Historial append-only de transición de cada necesidad.

    Append-only: la revocación de UPDATE/DELETE al rol de aplicación se
    aplica por DDL en migración posterior.
    """

    __tablename__ = "need_status_history"

    need_id = mapped_column(sa.Uuid, sa.ForeignKey("needs.id"), nullable=False)
    from_status = mapped_column(PgEnum(NeedStatus, "need_status"))
    to_status = mapped_column(PgEnum(NeedStatus, "need_status"), nullable=False)
    quantity_snapshot = mapped_column(JSONB)
    reason_code = mapped_column(sa.Text)
    actor_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    changed_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (sa.Index("ix_need_status_history_need_changed", "need_id", "changed_at"),)


class Validation(IdMixin, TimestampMixin, Base):
    """21. Acto de validación documental, remota, comunitaria o presencial."""

    __tablename__ = "validations"

    case_id = mapped_column(sa.Uuid, sa.ForeignKey("cases.id"), nullable=False)
    need_id = mapped_column(sa.Uuid, sa.ForeignKey("needs.id"))
    validator_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    validator_org_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"))
    type = mapped_column(PgEnum(ValidationType, "validation_type"), nullable=False)
    outcome = mapped_column(PgEnum(ValidationOutcome, "validation_outcome"))
    checklist_version = mapped_column(sa.Text)
    findings_enc = mapped_column(sa.LargeBinary)  # [SENS] jsonb cifrado
    evidence_id = mapped_column(sa.Uuid, sa.ForeignKey("evidence.id"))
    performed_at = mapped_column(sa.DateTime(timezone=True))
    expires_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_validations_case_type_performed", "case_id", "type", "performed_at"),
        sa.Index("ix_validations_need_outcome", "need_id", "outcome"),
    )


class Evidence(IdMixin, TimestampMixin, Base):
    """47. Registro probatorio común para caso, validación, entrega o auditoría."""

    __tablename__ = "evidence"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"), nullable=False)
    type = mapped_column(PgEnum(EvidenceType, "evidence_type"), nullable=False)
    media_asset_id = mapped_column(sa.Uuid, sa.ForeignKey("media_assets.id"))
    submitted_by_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    submitted_by_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    captured_at = mapped_column(sa.DateTime(timezone=True))
    location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))
    verification_status = mapped_column(
        PgEnum(EvidenceStatus, "evidence_status"),
        nullable=False,
        default=EvidenceStatus.PENDING,
    )
    visibility = mapped_column(
        PgEnum(EvidenceVisibility, "evidence_visibility"),
        nullable=False,
        default=EvidenceVisibility.PROTECTED,
    )
    redacted_derivative_id = mapped_column(sa.Uuid, sa.ForeignKey("media_assets.id"))
    # UNIQUE para duplicados exactos; índice perceptual vive en el módulo media.
    integrity_hash = mapped_column(sa.LargeBinary, unique=True)

    __table_args__ = (
        sa.Index("ix_evidence_incident_type_status", "incident_id", "type", "verification_status"),
    )


class EvidenceLink(IdMixin, TimestampMixin, Base):
    """48. Vincula evidencia de forma polimórfica sin duplicar archivos."""

    __tablename__ = "evidence_links"

    evidence_id = mapped_column(sa.Uuid, sa.ForeignKey("evidence.id"), nullable=False)
    entity_type = mapped_column(
        PgEnum(EvidenceEntityType, "evidence_entity_type"), nullable=False
    )
    entity_id = mapped_column(sa.Uuid, nullable=False)  # FK lógica polimórfica
    relation = mapped_column(PgEnum(EvidenceRelation, "evidence_relation"), nullable=False)
    is_primary = mapped_column(sa.Boolean, nullable=False, default=False)

    __table_args__ = (
        sa.UniqueConstraint("evidence_id", "entity_type", "entity_id", "relation"),
        sa.Index("ix_evidence_links_entity", "entity_type", "entity_id"),
    )


class DuplicateCandidate(IdMixin, TimestampMixin, Base):
    """51. Par de entidades posiblemente duplicadas con decisión humana."""

    __tablename__ = "duplicate_candidates"

    entity_type = mapped_column(
        PgEnum(DuplicateEntityType, "duplicate_entity_type"), nullable=False
    )
    left_id = mapped_column(sa.Uuid, nullable=False)  # FK lógica según entity_type
    right_id = mapped_column(sa.Uuid, nullable=False)  # FK lógica según entity_type
    algorithm_version = mapped_column(sa.Text, nullable=False)
    similarity_score = mapped_column(sa.Numeric)
    matched_features = mapped_column(JSONB)  # [SENS] acceso restringido
    status = mapped_column(
        PgEnum(DuplicateStatus, "duplicate_status"),
        nullable=False,
        default=DuplicateStatus.PROPOSED,
    )
    decided_by = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    decided_at = mapped_column(sa.DateTime(timezone=True))
    survivor_id = mapped_column(sa.Uuid)  # FK lógica según entity_type

    __table_args__ = (
        # UNIQUE(entity_type, least ids, greatest ids, algorithm_version).
        sa.Index(
            "uq_duplicate_candidates_pair",
            "entity_type",
            sa.text("least(left_id, right_id)"),
            sa.text("greatest(left_id, right_id)"),
            "algorithm_version",
            unique=True,
        ),
        sa.Index(
            "ix_duplicate_candidates_status_score",
            "status",
            sa.text("similarity_score DESC"),
        ),
    )
