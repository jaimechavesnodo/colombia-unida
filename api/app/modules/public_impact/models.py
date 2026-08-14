"""Experiencia pública y transparencia (tablas 53–55 §7.3 + content_reports §14.2).

Sigue las convenciones del módulo identity (EJEMPLAR):
- Enums Python con valores string estables + PgEnum(name="...").
- FKs cross-módulo por nombre de tabla en string.
- Nunca copiar campos protegidos directamente: solo proyecciones aprobadas.
"""

import enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column

from app.core.db import Base
from app.core.model_base import IdMixin, PgEnum, TimestampMixin

# ── Enums ──────────────────────────────────────────────────────────────


class PublicationStatus(enum.Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    PAUSED = "PAUSED"
    WITHDRAWN = "WITHDRAWN"
    ARCHIVED = "ARCHIVED"


class StoryItemType(enum.Enum):
    NEED = "NEED"
    UPDATE = "UPDATE"
    MATCH = "MATCH"
    DELIVERY = "DELIVERY"
    BEFORE_AFTER = "BEFORE_AFTER"
    COMPLETION = "COMPLETION"


class CtaType(enum.Enum):
    HELP_THIS_CASE = "HELP_THIS_CASE"
    HELP_HIGHEST_NEED = "HELP_HIGHEST_NEED"
    OFFER_RESOURCE = "OFFER_RESOURCE"
    SHARE = "SHARE"


class ModerationState(enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FLAGGED = "FLAGGED"


class ConsentBasis(enum.Enum):
    EXPLICIT_CONSENT = "EXPLICIT_CONSENT"
    LEGAL_BASIS_REVIEWED = "LEGAL_BASIS_REVIEWED"


class ContentReportStatus(enum.Enum):
    RECEIVED = "RECEIVED"
    REVIEWING = "REVIEWING"
    ACTIONED = "ACTIONED"
    DISMISSED = "DISMISSED"


# Instancia única: el tipo Postgres "publication_status" se usa en dos
# columnas (public_case_profiles y public_story_items).
PUBLICATION_STATUS = PgEnum(PublicationStatus, "publication_status")


# ── Tablas ─────────────────────────────────────────────────────────────


class PublicCaseProfile(IdMixin, TimestampMixin, Base):
    """53. Proyección deliberadamente desidentificada y aprobada de un caso."""

    __tablename__ = "public_case_profiles"

    # 0..1:1 con cases: a lo sumo un perfil público por caso.
    case_id = mapped_column(sa.Uuid, sa.ForeignKey("cases.id"), nullable=False, unique=True)
    slug = mapped_column(sa.Text, nullable=False, unique=True)
    display_title = mapped_column(sa.Text, nullable=False)
    story_summary = mapped_column(sa.Text)
    coarse_location = mapped_column(JSONB)  # [PUB] geohash/municipio, nunca punto exacto
    household_size_band = mapped_column(sa.Text)  # banda ("1-2", "3-5"), no conteo exacto
    cover_media_id = mapped_column(sa.Uuid)  # FK lógica a media (derivado aprobado)
    progress_percent = mapped_column(sa.Numeric)
    publication_status = mapped_column(
        PUBLICATION_STATUS, nullable=False, default=PublicationStatus.DRAFT
    )
    consent_basis = mapped_column(PgEnum(ConsentBasis, "consent_basis"), nullable=False)
    approved_by = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    published_at = mapped_column(sa.DateTime(timezone=True))
    next_review_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_public_case_profiles_status", "publication_status", "published_at"),
    )


class PublicStoryItem(IdMixin, TimestampMixin, Base):
    """54. Entradas del Impact Feed: necesidad, hito, entrega o antes/después."""

    __tablename__ = "public_story_items"

    profile_id = mapped_column(
        sa.Uuid, sa.ForeignKey("public_case_profiles.id"), nullable=False
    )
    type = mapped_column(PgEnum(StoryItemType, "story_item_type"), nullable=False)
    title = mapped_column(sa.Text, nullable=False)
    body = mapped_column(sa.Text)
    media_id = mapped_column(sa.Uuid)  # FK lógica a media (derivado aprobado)
    occurred_on = mapped_column(sa.Date)
    cta_type = mapped_column(PgEnum(CtaType, "cta_type"))
    cta_target = mapped_column(sa.Text)
    sort_key = mapped_column(sa.BigInteger, nullable=False, default=0)
    status = mapped_column(
        PUBLICATION_STATUS, nullable=False, default=PublicationStatus.DRAFT
    )
    moderation_state = mapped_column(
        PgEnum(ModerationState, "moderation_state"),
        nullable=False,
        default=ModerationState.PENDING,
    )

    __table_args__ = (
        # Feed público: status + orden descendente por sort_key.
        sa.Index("ix_public_story_items_status_sort", "status", "sort_key"),
        sa.Index("ix_public_story_items_profile", "profile_id"),
    )


class PublicImpactSnapshot(IdMixin, TimestampMixin, Base):
    """55. Métricas agregadas, fechadas y reproducibles para dashboard público."""

    __tablename__ = "public_impact_snapshots"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"), nullable=False)
    geography_level = mapped_column(sa.Text, nullable=False)
    geography_code = mapped_column(sa.Text, nullable=False)
    metric_code = mapped_column(sa.Text, nullable=False)
    # JSON canónico (claves ordenadas, sin espacios) almacenado como text en
    # lugar de JSONB para que participe de forma determinista en el UNIQUE.
    dimension_json = mapped_column(sa.Text, nullable=False, default="{}")
    value_numeric = mapped_column(sa.Numeric, nullable=False)
    as_of = mapped_column(sa.DateTime(timezone=True), nullable=False)
    privacy_threshold = mapped_column(sa.Integer)  # [INT] umbral k aplicado
    suppression_applied = mapped_column(sa.Boolean, nullable=False, default=False)
    source_run_id = mapped_column(sa.Uuid)  # FK lógica al run analítico que lo generó

    __table_args__ = (
        sa.UniqueConstraint(
            "incident_id",
            "geography_level",
            "geography_code",
            "metric_code",
            "dimension_json",
            "as_of",
        ),
    )


class ContentReport(IdMixin, TimestampMixin, Base):
    """Reporte ciudadano sobre contenido público (POST /public/v1/content-reports §14.2)."""

    __tablename__ = "content_reports"

    profile_id = mapped_column(sa.Uuid, sa.ForeignKey("public_case_profiles.id"))
    story_item_id = mapped_column(sa.Uuid, sa.ForeignKey("public_story_items.id"))
    reason_code = mapped_column(sa.Text, nullable=False)
    details_redacted = mapped_column(sa.Text)
    reporter_contact_enc = mapped_column(sa.LargeBinary)  # [SENS] contacto opcional cifrado
    status = mapped_column(
        PgEnum(ContentReportStatus, "content_report_status"),
        nullable=False,
        default=ContentReportStatus.RECEIVED,
    )

    __table_args__ = (
        # El reporte debe apuntar al menos a un perfil o a un story item.
        sa.CheckConstraint(
            "profile_id IS NOT NULL OR story_item_id IS NOT NULL",
            name="ck_content_reports_target_present",
        ),
        sa.Index("ix_content_reports_status_created", "status", "created_at"),
    )
