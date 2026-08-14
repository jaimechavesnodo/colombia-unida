"""Intake: conversaciones, mensajería, IA y plataforma (tablas 22–31 y 56–59 §7.3).

Sigue las convenciones del ejemplar `app.modules.identity.models`:
- Tabla y columnas en snake_case, nombres exactos del diccionario.
- Enums Python con valores string estables + PgEnum(name único global).
- FKs por nombre de tabla: sa.ForeignKey("conversations.id").
- Campos [PRIV]/[SENS] cifrados → sufijo _enc, sa.LargeBinary.
- Índices HMAC → sufijo _hmac, sa.LargeBinary.
- JSONB → sqlalchemy.dialects.postgresql.JSONB.
"""

import enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column

from app.core.db import Base
from app.core.model_base import IdMixin, PgEnum, TimestampMixin, VersionMixin, utcnow
from app.modules.identity.models import ChannelType  # no se redefine (§22 type)

__all__ = ["ChannelType"]  # re-export intencional; evita import sin uso

# ── Enums ──────────────────────────────────────────────────────────────


class ProviderType(enum.Enum):
    META_CLOUD_API = "META_CLOUD_API"
    INTERNAL_WEB = "INTERNAL_WEB"
    PARTNER_API = "PARTNER_API"


class ChannelStatus(enum.Enum):
    TEST = "TEST"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class ConversationStatus(enum.Enum):
    OPEN = "OPEN"
    WAITING_USER = "WAITING_USER"
    WAITING_SYSTEM = "WAITING_SYSTEM"
    AGENT_HANDOFF = "AGENT_HANDOFF"
    SNOOZED = "SNOOZED"
    CLOSED = "CLOSED"


class ConversationIntent(enum.Enum):
    NEED_HELP = "NEED_HELP"
    REPORT_FOR_OTHERS = "REPORT_FOR_OTHERS"
    OFFER_HELP = "OFFER_HELP"
    TRACK_CASE = "TRACK_CASE"
    OTHER = "OTHER"


class AgentMode(enum.Enum):
    BOT = "BOT"
    COPILOT = "COPILOT"
    HUMAN = "HUMAN"


class ParticipantType(enum.Enum):
    CONTACT = "CONTACT"
    REPORTER = "REPORTER"
    DONOR = "DONOR"
    AGENT = "AGENT"
    BOT = "BOT"
    SUPERVISOR = "SUPERVISOR"


class MessageDirection(enum.Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class MessageType(enum.Enum):
    TEXT = "TEXT"
    AUDIO = "AUDIO"
    IMAGE = "IMAGE"
    LOCATION = "LOCATION"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    INTERACTIVE = "INTERACTIVE"
    SYSTEM = "SYSTEM"


class MessageDeliveryStatus(enum.Enum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"


class MessageProcessingStatus(enum.Enum):
    RECEIVED = "RECEIVED"
    STORED = "STORED"
    SCANNING = "SCANNING"
    READY = "READY"
    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class StorageClassification(enum.Enum):
    QUARANTINE = "QUARANTINE"
    PROTECTED = "PROTECTED"
    PUBLIC_DERIVATIVE = "PUBLIC_DERIVATIVE"
    AUDIT = "AUDIT"


class MalwareStatus(enum.Enum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    ERROR = "ERROR"


class AiTaskType(enum.Enum):
    TRANSCRIBE = "TRANSCRIBE"
    EXTRACT_CASE = "EXTRACT_CASE"
    EXTRACT_OFFER = "EXTRACT_OFFER"
    SUMMARIZE = "SUMMARIZE"
    DUPLICATE_HINT = "DUPLICATE_HINT"
    IMAGE_TRIAGE = "IMAGE_TRIAGE"
    REDACT = "REDACT"


class AiRunStatus(enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class CandidateStatus(enum.Enum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class ConfirmationDecision(enum.Enum):
    ACCEPT = "ACCEPT"
    CORRECT = "CORRECT"
    REJECT = "REJECT"
    CANNOT_CONFIRM = "CANNOT_CONFIRM"


class QueuePriority(enum.Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class QueueItemStatus(enum.Enum):
    WAITING = "WAITING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    SNOOZED = "SNOOZED"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class WebhookProcessingStatus(enum.Enum):
    RECEIVED = "RECEIVED"
    DUPLICATE = "DUPLICATE"
    QUEUED = "QUEUED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class OutboxStatus(enum.Enum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"


class NotificationStatus(enum.Enum):
    QUEUED = "QUEUED"
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPPRESSED = "SUPPRESSED"


# ── Tablas ─────────────────────────────────────────────────────────────


class Channel(IdMixin, TimestampMixin, Base):
    """22. Configuración de un número/canal y sus credenciales referenciadas."""

    __tablename__ = "channels"

    type = mapped_column(PgEnum(ChannelType, "channel_type"), nullable=False)
    provider = mapped_column(PgEnum(ProviderType, "channel_provider_type"), nullable=False)
    external_account_id = mapped_column(sa.Text)
    phone_number_id = mapped_column(sa.Text)
    display_name = mapped_column(sa.Text, nullable=False)
    status = mapped_column(
        PgEnum(ChannelStatus, "channel_status"), nullable=False, default=ChannelStatus.TEST
    )
    secret_ref = mapped_column(sa.Text)  # referencia a Secret Manager; nunca el secreto
    config = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        sa.UniqueConstraint("provider", "external_account_id", "phone_number_id"),
    )


class Conversation(IdMixin, TimestampMixin, VersionMixin, Base):
    """23. Sesión lógica durable; sobrevive interrupciones y varios intents/casos."""

    __tablename__ = "conversations"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"))
    channel_id = mapped_column(sa.Uuid, sa.ForeignKey("channels.id"), nullable=False)
    external_thread_key_hmac = mapped_column(sa.LargeBinary)
    status = mapped_column(
        PgEnum(ConversationStatus, "conversation_status"),
        nullable=False,
        default=ConversationStatus.OPEN,
    )
    primary_intent = mapped_column(PgEnum(ConversationIntent, "conversation_intent"))
    language_code = mapped_column(sa.String(10), nullable=False, default="es")
    active_case_id = mapped_column(sa.Uuid)  # FK lógica a cases (módulo cases)
    last_message_at = mapped_column(sa.DateTime(timezone=True))
    agent_mode = mapped_column(
        PgEnum(AgentMode, "agent_mode"), nullable=False, default=AgentMode.BOT
    )
    context_summary_enc = mapped_column(sa.LargeBinary)

    __table_args__ = (
        sa.Index(
            "ix_conversations_channel_thread_status",
            "channel_id",
            "external_thread_key_hmac",
            "status",
        ),
        sa.Index("ix_conversations_last_message_status", "last_message_at", "status"),
    )


class ConversationParticipant(IdMixin, TimestampMixin, Base):
    """24. Participantes humanos, agentes o bots y su identidad."""

    __tablename__ = "conversation_participants"

    conversation_id = mapped_column(sa.Uuid, sa.ForeignKey("conversations.id"), nullable=False)
    person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    participant_type = mapped_column(PgEnum(ParticipantType, "participant_type"), nullable=False)
    joined_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    left_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_conversation_participants_conv_joined", "conversation_id", "joined_at"),
        # UNIQUE participante activo por tipo/identidad (parcial: solo left_at IS NULL)
        sa.Index(
            "uq_conversation_participants_active",
            "conversation_id",
            "participant_type",
            "person_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("left_at IS NULL"),
        ),
    )


class Message(IdMixin, TimestampMixin, Base):
    """25. Registro canónico de mensajes entrantes/salientes y estados de entrega.

    Partición mensual prevista a nivel DDL/ops (no modelada en ORM).
    """

    __tablename__ = "messages"

    conversation_id = mapped_column(sa.Uuid, sa.ForeignKey("conversations.id"), nullable=False)
    provider_message_id = mapped_column(sa.Text, unique=True)
    direction = mapped_column(PgEnum(MessageDirection, "message_direction"), nullable=False)
    type = mapped_column(PgEnum(MessageType, "message_type"), nullable=False)
    text_enc = mapped_column(sa.LargeBinary)
    normalized_text_redacted = mapped_column(sa.Text)
    media_asset_id = mapped_column(sa.Uuid)  # FK lógica a media_assets (circular con message_id)
    reply_to_message_id = mapped_column(sa.Uuid, sa.ForeignKey("messages.id"))
    provider_timestamp = mapped_column(sa.DateTime(timezone=True))
    received_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    delivery_status = mapped_column(PgEnum(MessageDeliveryStatus, "message_delivery_status"))
    processing_status = mapped_column(
        PgEnum(MessageProcessingStatus, "message_processing_status"),
        nullable=False,
        default=MessageProcessingStatus.RECEIVED,
    )
    idempotency_key = mapped_column(sa.Text)

    __table_args__ = (
        sa.Index("ix_messages_conversation_provider_ts", "conversation_id", "provider_timestamp"),
    )


class MediaAsset(IdMixin, TimestampMixin, Base):
    """26. Metadatos de audio, fotos, ubicación, video o documento fuera de DB."""

    __tablename__ = "media_assets"

    message_id = mapped_column(sa.Uuid, sa.ForeignKey("messages.id"), nullable=False)
    bucket_class = mapped_column(
        PgEnum(StorageClassification, "storage_classification"),
        nullable=False,
        default=StorageClassification.QUARANTINE,
    )
    object_uri = mapped_column(sa.Text, nullable=False)
    mime_type = mapped_column(sa.Text, nullable=False)
    size_bytes = mapped_column(sa.BigInteger)
    sha256 = mapped_column(sa.LargeBinary, unique=True)  # UNIQUE(sha256); scope incidente: ops
    perceptual_hash = mapped_column(sa.LargeBinary, index=True)
    malware_status = mapped_column(
        PgEnum(MalwareStatus, "malware_status"), nullable=False, default=MalwareStatus.PENDING
    )
    exif_removed = mapped_column(sa.Boolean, nullable=False, default=False)
    retention_until = mapped_column(sa.DateTime(timezone=True), index=True)
    transcript_id = mapped_column(sa.Uuid)  # FK lógica a ai_extraction_runs (TRANSCRIBE)


class AiExtractionRun(IdMixin, TimestampMixin, Base):
    """27. Ejecución reproducible de STT/LLM/visión con versión, costos y confianza."""

    __tablename__ = "ai_extraction_runs"

    message_id = mapped_column(sa.Uuid, sa.ForeignKey("messages.id"), nullable=False)
    task_type = mapped_column(PgEnum(AiTaskType, "ai_task_type"), nullable=False)
    provider = mapped_column(sa.Text, nullable=False)
    model_version = mapped_column(sa.Text, nullable=False)
    prompt_version = mapped_column(sa.Text, nullable=False)
    input_hash = mapped_column(sa.LargeBinary, nullable=False)
    output_json_enc = mapped_column(sa.LargeBinary)  # jsonb cifrado → bytea por convención _enc
    confidence = mapped_column(sa.Numeric)
    safety_flags = mapped_column(JSONB, nullable=False, default=dict)
    latency_ms = mapped_column(sa.Integer)
    cost_units = mapped_column(sa.Numeric)
    status = mapped_column(
        PgEnum(AiRunStatus, "ai_run_status"), nullable=False, default=AiRunStatus.QUEUED
    )

    __table_args__ = (
        sa.Index("ix_ai_runs_message_task_created", "message_id", "task_type", "created_at"),
        # cache seguro de inferencias
        sa.UniqueConstraint("input_hash", "task_type", "model_version", "prompt_version"),
    )


class ExtractionCandidate(IdMixin, TimestampMixin, Base):
    """28. Campo estructurado propuesto por IA antes de confirmación."""

    __tablename__ = "extraction_candidates"

    ai_run_id = mapped_column(sa.Uuid, sa.ForeignKey("ai_extraction_runs.id"), nullable=False)
    target_entity_type = mapped_column(sa.Text, nullable=False)
    field_path = mapped_column(sa.Text, nullable=False)
    proposed_value_enc = mapped_column(sa.LargeBinary)  # jsonb cifrado → bytea (_enc)
    normalized_value = mapped_column(JSONB)
    confidence = mapped_column(sa.Numeric)
    provenance_offsets = mapped_column(JSONB)
    status = mapped_column(
        PgEnum(CandidateStatus, "candidate_status"),
        nullable=False,
        default=CandidateStatus.PROPOSED,
    )

    __table_args__ = (
        sa.Index("ix_extraction_candidates_run_status", "ai_run_id", "status"),
        sa.Index("ix_extraction_candidates_entity_field", "target_entity_type", "field_path"),
    )


class HumanConfirmation(IdMixin, TimestampMixin, Base):
    """29. Decisión humana o del propio reportante sobre un dato propuesto.

    Se conservan todas las decisiones; la vigente se deriva por decided_at.
    """

    __tablename__ = "human_confirmations"

    candidate_id = mapped_column(
        sa.Uuid, sa.ForeignKey("extraction_candidates.id"), nullable=False
    )
    actor_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    actor_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    decision = mapped_column(
        PgEnum(ConfirmationDecision, "confirmation_decision"), nullable=False
    )
    corrected_value_enc = mapped_column(sa.LargeBinary)  # jsonb cifrado → bytea (_enc)
    confirmation_message_id = mapped_column(sa.Uuid, sa.ForeignKey("messages.id"))
    decided_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        sa.Index("ix_human_confirmations_candidate_decided", "candidate_id", "decided_at"),
    )


class AgentQueueItem(IdMixin, TimestampMixin, Base):
    """30. Trabajo priorizado para atención humana."""

    __tablename__ = "agent_queue_items"

    conversation_id = mapped_column(sa.Uuid, sa.ForeignKey("conversations.id"))
    case_id = mapped_column(sa.Uuid)  # FK lógica a cases (módulo cases)
    queue_code = mapped_column(sa.Text, nullable=False)
    priority = mapped_column(
        PgEnum(QueuePriority, "queue_priority"), nullable=False, default=QueuePriority.P2
    )
    reason_code = mapped_column(sa.Text)
    status = mapped_column(
        PgEnum(QueueItemStatus, "queue_item_status"),
        nullable=False,
        default=QueueItemStatus.WAITING,
    )
    sla_due_at = mapped_column(sa.DateTime(timezone=True))
    required_skill = mapped_column(sa.Text)

    __table_args__ = (
        sa.Index(
            "ix_agent_queue_items_dispatch", "queue_code", "status", "priority", "sla_due_at"
        ),
    )


class AgentAssignment(IdMixin, TimestampMixin, Base):
    """31. Asignación y ocupación de agentes con trazabilidad."""

    __tablename__ = "agent_assignments"

    queue_item_id = mapped_column(sa.Uuid, sa.ForeignKey("agent_queue_items.id"), nullable=False)
    agent_user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), nullable=False)
    assigned_by = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    assigned_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    accepted_at = mapped_column(sa.DateTime(timezone=True))
    released_at = mapped_column(sa.DateTime(timezone=True))
    outcome = mapped_column(sa.Text)  # catalogado a nivel aplicación

    __table_args__ = (
        sa.Index("ix_agent_assignments_agent_released", "agent_user_id", "released_at"),
        # una asignación activa por item
        sa.Index(
            "uq_agent_assignments_active_item",
            "queue_item_id",
            unique=True,
            postgresql_where=sa.text("released_at IS NULL"),
        ),
    )


class WebhookReceipt(IdMixin, TimestampMixin, Base):
    """56. Persistencia idempotente del envelope externo antes de procesamiento.

    Partición mensual prevista a nivel DDL/ops; retención corta del payload bruto.
    """

    __tablename__ = "webhook_receipts"

    channel_id = mapped_column(sa.Uuid, sa.ForeignKey("channels.id"), nullable=False)
    provider_event_id = mapped_column(sa.Text, nullable=False)
    event_type = mapped_column(sa.Text)
    signature_valid = mapped_column(sa.Boolean, nullable=False, default=False)
    payload_enc = mapped_column(sa.LargeBinary)  # jsonb cifrado → bytea (_enc)
    received_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    acked_at = mapped_column(sa.DateTime(timezone=True))
    processing_status = mapped_column(
        PgEnum(WebhookProcessingStatus, "webhook_processing_status"),
        nullable=False,
        default=WebhookProcessingStatus.RECEIVED,
    )
    attempt_count = mapped_column(sa.Integer, nullable=False, default=0)
    last_error_redacted = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint("channel_id", "provider_event_id"),
        sa.Index("ix_webhook_receipts_status_received", "processing_status", "received_at"),
    )


class OutboxEvent(IdMixin, TimestampMixin, Base):
    """57. Eventos de dominio confirmados en la misma transacción del cambio de negocio."""

    __tablename__ = "outbox_events"

    aggregate_type = mapped_column(sa.Text, nullable=False)
    aggregate_id = mapped_column(sa.Uuid, nullable=False)  # FK lógica al agregado de origen
    event_type = mapped_column(sa.Text, nullable=False)
    schema_version = mapped_column(sa.Integer, nullable=False, default=1)
    payload_redacted = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    publish_status = mapped_column(
        PgEnum(OutboxStatus, "outbox_status"), nullable=False, default=OutboxStatus.PENDING
    )
    published_at = mapped_column(sa.DateTime(timezone=True))
    retry_count = mapped_column(sa.Integer, nullable=False, default=0)
    correlation_id = mapped_column(sa.Uuid)
    # Extensión operativa del outbox (worker de publicación; no está en el diccionario):
    next_attempt_at = mapped_column(sa.DateTime(timezone=True))
    locked_at = mapped_column(sa.DateTime(timezone=True))
    last_error_redacted = mapped_column(sa.Text)

    __table_args__ = (
        sa.Index("ix_outbox_events_status_occurred", "publish_status", "occurred_at"),
        # apoyo al worker: barrido de pendientes/reintentos
        sa.Index("ix_outbox_events_status_next_attempt", "publish_status", "next_attempt_at"),
    )


class IdempotencyKey(Base):
    """58. Evita duplicar comandos externos y operaciones críticas.

    PK compuesta (scope, key); TTL por expires_at. Rechazar misma key con
    request_hash distinto (regla de aplicación).
    """

    __tablename__ = "idempotency_keys"

    scope = mapped_column(sa.Text, primary_key=True)
    key = mapped_column(sa.Text, primary_key=True)
    request_hash = mapped_column(sa.LargeBinary, nullable=False)
    response_code = mapped_column(sa.Integer)
    response_body_redacted = mapped_column(JSONB)
    resource_type = mapped_column(sa.Text)
    resource_id = mapped_column(sa.Text)
    expires_at = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    created_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)


class Notification(IdMixin, TimestampMixin, Base):
    """59. Orden y resultado de mensajes transaccionales (WhatsApp/email/SMS)."""

    __tablename__ = "notifications"

    recipient_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"), nullable=False)
    channel_id = mapped_column(sa.Uuid, sa.ForeignKey("channels.id"), nullable=False)
    template_code = mapped_column(sa.Text, nullable=False)
    template_version = mapped_column(sa.Text, nullable=False)
    variables_enc = mapped_column(sa.LargeBinary)  # jsonb cifrado → bytea (_enc)
    status = mapped_column(
        PgEnum(NotificationStatus, "notification_status"),
        nullable=False,
        default=NotificationStatus.QUEUED,
    )
    scheduled_at = mapped_column(sa.DateTime(timezone=True))
    sent_at = mapped_column(sa.DateTime(timezone=True))
    provider_message_id = mapped_column(sa.Text)
    failure_code = mapped_column(sa.Text)
    # "UNIQUE idempotency ref por evento/recipient/template" (§59): clave derivada
    # evento+recipient+template calculada en aplicación.
    idempotency_key = mapped_column(sa.Text, unique=True)

    __table_args__ = (sa.Index("ix_notifications_status_scheduled", "status", "scheduled_at"),)
