"""Identity & Access + personas, hogares y territorio (tablas 01–12 §7.3).

EJEMPLAR DE CONVENCIONES para todos los módulos de modelos:
- Tabla y columnas en snake_case, nombres exactos del diccionario.
- Enums Python con valores string estables + PgEnum(name="<entidad>_<campo>").
- FKs por nombre de tabla: sa.ForeignKey("persons.id").
- Campos [PRIV]/[SENS] cifrados → sufijo _enc, sa.LargeBinary.
- Índices HMAC → sufijo _hmac, sa.LargeBinary.
- Geometrías → geoalchemy2.Geometry (SRID 4326).
- JSONB → sqlalchemy.dialects.postgresql.JSONB.
"""

import enum

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column

from app.core.db import Base
from app.core.model_base import IdMixin, PgEnum, TimestampMixin, utcnow

# ── Enums ──────────────────────────────────────────────────────────────


class IncidentStatus(enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RECOVERY = "RECOVERY"
    CLOSED = "CLOSED"
    ARCHIVED = "ARCHIVED"


class OrgType(enum.Enum):
    NGO = "NGO"
    GOVERNMENT = "GOVERNMENT"
    COMPANY = "COMPANY"
    COMMUNITY = "COMMUNITY"
    OPERATOR = "OPERATOR"
    FUNDER = "FUNDER"
    LOGISTICS = "LOGISTICS"


class VerificationStatus(enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


class RecordStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class UserStatus(enum.Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class RoleCode(enum.Enum):
    AFFECTED_SELF_SERVICE = "AFFECTED_SELF_SERVICE"
    REPORTER = "REPORTER"
    COMMUNITY_LEADER = "COMMUNITY_LEADER"
    VOLUNTEER = "VOLUNTEER"
    VALIDATOR = "VALIDATOR"
    DONOR = "DONOR"
    COMPANY_REP = "COMPANY_REP"
    ORG_OPERATOR = "ORG_OPERATOR"
    AGENT = "AGENT"
    SUPERVISOR = "SUPERVISOR"
    AUDITOR = "AUDITOR"
    ADMIN = "ADMIN"


class MembershipStatus(enum.Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ENDED = "ENDED"


class DataStatus(enum.Enum):
    PARTIAL = "PARTIAL"
    CONFIRMED = "CONFIRMED"
    MERGED = "MERGED"
    RESTRICTED = "RESTRICTED"
    DELETED_TOMBSTONE = "DELETED_TOMBSTONE"


class IdentifierType(enum.Enum):
    PHONE = "PHONE"
    NATIONAL_ID = "NATIONAL_ID"
    PASSPORT = "PASSPORT"
    EMAIL = "EMAIL"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    OTHER = "OTHER"


class ConsentPurpose(enum.Enum):
    CASE_MANAGEMENT = "CASE_MANAGEMENT"
    CONTACT = "CONTACT"
    AI_PROCESSING = "AI_PROCESSING"
    LOCATION = "LOCATION"
    PUBLICATION = "PUBLICATION"
    DATA_SHARING = "DATA_SHARING"


class ConsentStatus(enum.Enum):
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    EXCEPTION_REVIEW = "EXCEPTION_REVIEW"


class ChannelType(enum.Enum):
    WHATSAPP = "WHATSAPP"
    WEB = "WEB"
    API = "API"


class HouseholdStatus(enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    CLOSED = "CLOSED"
    RESTRICTED = "RESTRICTED"


class HouseholdRelation(enum.Enum):
    HEAD = "HEAD"
    PARTNER = "PARTNER"
    CHILD = "CHILD"
    DEPENDENT = "DEPENDENT"
    RELATIVE = "RELATIVE"
    NON_RELATIVE = "NON_RELATIVE"
    UNKNOWN = "UNKNOWN"


class LocationSource(enum.Enum):
    GPS = "GPS"
    WHATSAPP_LOCATION = "WHATSAPP_LOCATION"
    GEOCODED_TEXT = "GEOCODED_TEXT"
    MANUAL = "MANUAL"
    IMPORTED = "IMPORTED"


# ── Tablas ─────────────────────────────────────────────────────────────


class Incident(IdMixin, TimestampMixin, Base):
    """01. Evento o programa humanitario que delimita datos y reglas."""

    __tablename__ = "incidents"

    code = mapped_column(sa.String(24), nullable=False, unique=True)
    name = mapped_column(sa.Text, nullable=False)
    status = mapped_column(
        PgEnum(IncidentStatus, "incident_status"), nullable=False, default=IncidentStatus.DRAFT
    )
    starts_at = mapped_column(sa.DateTime(timezone=True))
    ends_at = mapped_column(sa.DateTime(timezone=True))
    geography = mapped_column(Geometry(srid=4326, spatial_index=True))
    policy_version = mapped_column(sa.Text)

    __table_args__ = (sa.Index("ix_incidents_status_starts", "status", "starts_at"),)


class Organization(IdMixin, TimestampMixin, Base):
    """02. Aliado, entidad operadora, empresa, fundación o autoridad."""

    __tablename__ = "organizations"

    legal_name_enc = mapped_column(sa.LargeBinary)
    public_name = mapped_column(sa.Text, nullable=False)
    org_type = mapped_column(PgEnum(OrgType, "org_type"), nullable=False)
    tax_id_enc = mapped_column(sa.LargeBinary)
    tax_id_hmac = mapped_column(sa.LargeBinary, index=True)
    verification_status = mapped_column(
        PgEnum(VerificationStatus, "org_verification_status"),
        nullable=False,
        default=VerificationStatus.PENDING,
    )
    contact_email_enc = mapped_column(sa.LargeBinary)
    status = mapped_column(
        PgEnum(RecordStatus, "org_record_status"), nullable=False, default=RecordStatus.ACTIVE
    )

    __table_args__ = (sa.UniqueConstraint("public_name", "org_type"),)


class User(IdMixin, TimestampMixin, Base):
    """03. Cuenta autenticada para personal interno."""

    __tablename__ = "users"

    person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    identity_provider_sub = mapped_column(sa.Text, unique=True)
    email_enc = mapped_column(sa.LargeBinary)
    email_hmac = mapped_column(sa.LargeBinary, unique=True)
    password_hash = mapped_column(sa.Text)  # argon2/bcrypt; ADR-0002 (auth propia + TOTP)
    totp_secret_enc = mapped_column(sa.LargeBinary)
    mfa_enrolled = mapped_column(sa.Boolean, nullable=False, default=False)
    status = mapped_column(
        PgEnum(UserStatus, "user_status"), nullable=False, default=UserStatus.INVITED
    )
    last_login_at = mapped_column(sa.DateTime(timezone=True))
    authz_version = mapped_column(sa.BigInteger, nullable=False, default=1)

    __table_args__ = (sa.Index("ix_users_status", "status"),)


class Role(IdMixin, TimestampMixin, Base):
    """04. Catálogo de roles y capacidades base."""

    __tablename__ = "roles"

    code = mapped_column(PgEnum(RoleCode, "role_code"), nullable=False)
    name = mapped_column(sa.Text, nullable=False)
    permissions = mapped_column(JSONB, nullable=False, default=dict)
    system_managed = mapped_column(sa.Boolean, nullable=False, default=True)
    version = mapped_column(sa.Integer, nullable=False, default=1)

    __table_args__ = (sa.UniqueConstraint("code", "version"),)


class OrganizationMembership(IdMixin, TimestampMixin, Base):
    """05. Vincula cuenta y organización con estado y vigencia."""

    __tablename__ = "organization_memberships"

    organization_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"), nullable=False)
    user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), nullable=False)
    membership_type = mapped_column(sa.Text, nullable=False, default="MEMBER")
    status = mapped_column(
        PgEnum(MembershipStatus, "membership_status"),
        nullable=False,
        default=MembershipStatus.INVITED,
    )
    valid_from = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    valid_to = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint("organization_id", "user_id", "membership_type"),
        sa.Index("ix_org_memberships_user_status", "user_id", "status"),
    )


class UserRoleAssignment(IdMixin, TimestampMixin, Base):
    """06. Rol con alcance por incidente, organización y/o territorio."""

    __tablename__ = "user_role_assignments"

    user_id = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), nullable=False)
    role_id = mapped_column(sa.Uuid, sa.ForeignKey("roles.id"), nullable=False)
    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"))
    organization_id = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id"))
    geography_scope = mapped_column(Geometry(srid=4326, spatial_index=True))
    valid_from = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    valid_to = mapped_column(sa.DateTime(timezone=True))
    granted_by = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))

    __table_args__ = (sa.Index("ix_user_role_user_incident", "user_id", "incident_id"),)


class Person(IdMixin, TimestampMixin, Base):
    """07. Individuo del ecosistema; múltiples roles y casos."""

    __tablename__ = "persons"

    display_name_enc = mapped_column(sa.LargeBinary)
    preferred_name_enc = mapped_column(sa.LargeBinary)
    birth_date_enc = mapped_column(sa.LargeBinary)
    gender_optional_enc = mapped_column(sa.LargeBinary)
    language_code = mapped_column(sa.String(10), nullable=False, default="es")
    vulnerability_flags_enc = mapped_column(sa.LargeBinary)
    deceased_flag = mapped_column(sa.Boolean, nullable=False, default=False)
    data_status = mapped_column(
        PgEnum(DataStatus, "person_data_status"), nullable=False, default=DataStatus.PARTIAL
    )
    # HMAC de nombre normalizado para dedupe controlado (§7.3-07)
    name_dedupe_hmac = mapped_column(sa.LargeBinary, index=True)
    merged_into_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))


class PersonIdentifier(IdMixin, TimestampMixin, Base):
    """08. Identificadores cifrados, separados del perfil."""

    __tablename__ = "person_identifiers"

    person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"), nullable=False)
    type = mapped_column(PgEnum(IdentifierType, "identifier_type"), nullable=False)
    value_enc = mapped_column(sa.LargeBinary, nullable=False)
    value_hmac = mapped_column(sa.LargeBinary, nullable=False)
    last4 = mapped_column(sa.Text)
    verified_at = mapped_column(sa.DateTime(timezone=True))
    issuer = mapped_column(sa.Text)
    is_primary = mapped_column(sa.Boolean, nullable=False, default=False)

    __table_args__ = (
        sa.UniqueConstraint("type", "value_hmac"),
        sa.Index("ix_person_identifiers_person_primary", "person_id", "is_primary"),
    )


class Consent(IdMixin, TimestampMixin, Base):
    """09. Prueba versionada de autorización, revocación y finalidades."""

    __tablename__ = "consents"

    person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"), nullable=False)
    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"))
    purpose = mapped_column(PgEnum(ConsentPurpose, "consent_purpose"), nullable=False)
    notice_version = mapped_column(sa.Text, nullable=False)
    status = mapped_column(PgEnum(ConsentStatus, "consent_status"), nullable=False)
    captured_via = mapped_column(PgEnum(ChannelType, "consent_channel_type"), nullable=False)
    proof_message_id = mapped_column(sa.Uuid)  # FK lógica a messages (módulo intake)
    granted_at = mapped_column(sa.DateTime(timezone=True))
    revoked_at = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (sa.Index("ix_consents_person_purpose", "person_id", "purpose", "status"),)


class Household(IdMixin, TimestampMixin, Base):
    """10. Unidad familiar afectada; no equivale a un número de WhatsApp."""

    __tablename__ = "households"

    incident_id = mapped_column(sa.Uuid, sa.ForeignKey("incidents.id"), nullable=False)
    reference_code = mapped_column(sa.String(32), nullable=False)
    head_person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"))
    member_count = mapped_column(sa.Integer)
    minors_count = mapped_column(sa.Integer)
    older_adults_count = mapped_column(sa.Integer)
    disability_count = mapped_column(sa.Integer)
    status = mapped_column(
        PgEnum(HouseholdStatus, "household_status"), nullable=False, default=HouseholdStatus.DRAFT
    )
    location_id = mapped_column(sa.Uuid, sa.ForeignKey("locations.id"))

    __table_args__ = (
        sa.UniqueConstraint("incident_id", "reference_code"),
        sa.CheckConstraint(
            "member_count IS NULL OR minors_count IS NULL OR member_count >= minors_count",
            name="ck_households_counts_coherent",
        ),
    )


class HouseholdMember(IdMixin, TimestampMixin, Base):
    """11. Membresía temporal de personas en hogares."""

    __tablename__ = "household_members"

    household_id = mapped_column(sa.Uuid, sa.ForeignKey("households.id"), nullable=False)
    person_id = mapped_column(sa.Uuid, sa.ForeignKey("persons.id"), nullable=False)
    relationship = mapped_column(
        PgEnum(HouseholdRelation, "household_relation"),
        nullable=False,
        default=HouseholdRelation.UNKNOWN,
    )
    is_primary_contact = mapped_column(sa.Boolean, nullable=False, default=False)
    valid_from = mapped_column(sa.Date)
    valid_to = mapped_column(sa.Date)
    source_report_id = mapped_column(sa.Uuid)  # FK lógica a reports (módulo cases)

    __table_args__ = (
        sa.UniqueConstraint("household_id", "person_id", "valid_from"),
        sa.Index("ix_household_members_person", "person_id", "valid_to"),
    )


class GeoDivipola(Base):
    """Referencia DIVIPOLA (DANE): departamentos y municipios.

    Tabla de referencia agregada a la plataforma (no está en el diccionario
    §7.3): soporta la normalización de geografía exigida por §9.1 y los
    filtros seguros por departamento/municipio del plano público.
    Fuente: datos.gov.co dataset gdxc-w37w.
    """

    __tablename__ = "geo_divipola"

    municipality_code = mapped_column(sa.String(5), primary_key=True)  # cod_mpio DANE
    department_code = mapped_column(sa.String(2), nullable=False, index=True)
    department_name = mapped_column(sa.Text, nullable=False)
    municipality_name = mapped_column(sa.Text, nullable=False)
    municipality_type = mapped_column(sa.Text)
    longitude = mapped_column(sa.Numeric)
    latitude = mapped_column(sa.Numeric)


class Location(IdMixin, TimestampMixin, Base):
    """12. Ubicación normalizada con precisión y procedencia explícitas."""

    __tablename__ = "locations"

    country_code = mapped_column(sa.CHAR(2), nullable=False, default="CO")
    admin1 = mapped_column(sa.Text)  # departamento
    admin2 = mapped_column(sa.Text)  # municipio
    admin3 = mapped_column(sa.Text)  # vereda/corregimiento
    locality_text_enc = mapped_column(sa.LargeBinary)
    point = mapped_column(Geometry(geometry_type="POINT", srid=4326, spatial_index=True))
    accuracy_m = mapped_column(sa.Numeric)
    source = mapped_column(PgEnum(LocationSource, "location_source"), nullable=False)
    public_geohash = mapped_column(sa.String(8))
    captured_at = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (sa.Index("ix_locations_admin", "admin1", "admin2"),)
