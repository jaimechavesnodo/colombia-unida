"""Lógica de dominio del módulo de casos (M4, §5.1 y §8 del alcance).

Funciones puras sobre una Session; el llamador (router, bot o worker)
decide cuándo hacer commit. Toda transición de estado pasa por
`app.core.state_machines.assert_transition` y deja historia append-only
+ evento en el outbox dentro de la misma transacción.
"""

import logging
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.logging import log_ctx
from app.core.model_base import utcnow
from app.core.outbox import publish
from app.core.security import encrypt_json
from app.core.state_machines import assert_transition
from app.modules.cases.models import (
    Case,
    CasePerson,
    CaseStatus,
    CaseStatusHistory,
    DuplicateCandidate,
    DuplicateEntityType,
    DuplicateStatus,
    Need,
    NeedCatalog,
    NeedStatus,
    NeedStatusHistory,
    Report,
    ReportStatus,
    ReportSubject,
    SubjectType,
    Validation,
    ValidationOutcome,
    ValidationType,
)
from app.modules.identity.models import Household, Location, User

logger = logging.getLogger("cases")

ALGORITHM_VERSION = "v1"

# Estados de caso con nombre de evento propio; el resto usa case.status_changed.
_CASE_EVENT_BY_STATUS = {
    CaseStatus.VERIFIED: "case.verified",
    CaseStatus.CLOSED: "case.closed",
    CaseStatus.INCOMPLETE: "case.incomplete",
}

_NEED_EVENT_BY_STATUS = {
    NeedStatus.VERIFIED: "need.verified",
    NeedStatus.PARTIALLY_COVERED: "need.coverage_changed",
    NeedStatus.COVERED: "need.coverage_changed",
    NeedStatus.DELIVERED_VERIFIED: "need.delivered_verified",
}


def _num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


# ── Transiciones ───────────────────────────────────────────────────────


def transition_case(
    session: Session,
    case: Case,
    to_status: str | CaseStatus,
    actor_user_id,
    reason_code: str | None = None,
    note: str | None = None,
) -> Case:
    """Transición validada de caso: historia append-only + evento outbox."""
    target = to_status if isinstance(to_status, CaseStatus) else CaseStatus(to_status)
    assert_transition("case", case.status, target)

    session.add(
        CaseStatusHistory(
            case_id=case.id,
            from_status=case.status,
            to_status=target,
            reason_code=reason_code,
            note_redacted=note,
            changed_by_user_id=actor_user_id,
            changed_at=utcnow(),
        )
    )
    event_type = _CASE_EVENT_BY_STATUS.get(target, "case.status_changed")
    publish(
        session,
        event_type=event_type,
        aggregate_type="case",
        aggregate_id=case.id,
        payload={"case_code": case.case_code, "to": target.value},
    )
    case.status = target
    if target == CaseStatus.CLOSED:
        case.closed_at = utcnow()
    log_ctx(logger, logging.INFO, "case transition", case_code=case.case_code, to=target.value)
    return case


def transition_need(
    session: Session,
    need: Need,
    to_status: str | NeedStatus,
    actor_user_id,
    reason_code: str | None = None,
) -> Need:
    """Transición validada de necesidad: historia append-only + evento."""
    target = to_status if isinstance(to_status, NeedStatus) else NeedStatus(to_status)
    assert_transition("need", need.status, target)

    session.add(
        NeedStatusHistory(
            need_id=need.id,
            from_status=need.status,
            to_status=target,
            quantity_snapshot={
                "requested": _num(need.requested_qty),
                "confirmed": _num(need.confirmed_qty),
                "covered": _num(need.covered_qty),
            },
            reason_code=reason_code,
            actor_user_id=actor_user_id,
            changed_at=utcnow(),
        )
    )
    event_type = _NEED_EVENT_BY_STATUS.get(target, "need.status_changed")
    publish(
        session,
        event_type=event_type,
        aggregate_type="need",
        aggregate_id=need.id,
        payload={"to": target.value},
    )
    need.status = target
    return need


# ── Consolidación de reportes (CASE-02) ────────────────────────────────


def link_report_to_case(
    session: Session,
    report: Report,
    case: Case,
    subject_type: str | SubjectType = "HOUSEHOLD",
) -> ReportSubject:
    """Vincula un reporte a un caso existente SIN descartarlo (CASE-02)."""
    stype = subject_type if isinstance(subject_type, SubjectType) else SubjectType(subject_type)
    assert_transition("report", report.status, ReportStatus.LINKED)

    subject = ReportSubject(report_id=report.id, case_id=case.id, subject_type=stype)
    session.add(subject)
    report.status = ReportStatus.LINKED
    publish(
        session,
        event_type="report.linked_to_case",
        aggregate_type="report",
        aggregate_id=report.id,
        payload={"case_code": case.case_code},
    )
    return subject


# ── Validaciones (SoD) ─────────────────────────────────────────────────


def create_validation(
    session: Session,
    case: Case,
    validator_user: User,
    vtype: str | ValidationType,
    outcome: str | ValidationOutcome,
    need: Need | None = None,
    checklist_version: str = "v1",
    findings: Any | None = None,
    evidence_id=None,
) -> Validation:
    """Registra una validación; PASS promueve el caso a VERIFIED.

    SoD: quien llevó el caso a PENDING_VERIFICATION no puede validarlo.
    """
    vtype = vtype if isinstance(vtype, ValidationType) else ValidationType(vtype)
    outcome = outcome if isinstance(outcome, ValidationOutcome) else ValidationOutcome(outcome)

    submitted_by_same = session.execute(
        sa.select(CaseStatusHistory.id).where(
            CaseStatusHistory.case_id == case.id,
            CaseStatusHistory.changed_by_user_id == validator_user.id,
            CaseStatusHistory.to_status == CaseStatus.PENDING_VERIFICATION,
        )
    ).first()
    if submitted_by_same is not None:
        raise ValueError("SoD")

    validation = Validation(
        case_id=case.id,
        need_id=need.id if need is not None else None,
        validator_user_id=validator_user.id,
        type=vtype,
        outcome=outcome,
        checklist_version=checklist_version,
        findings_enc=encrypt_json(findings) if findings is not None else None,
        evidence_id=evidence_id,
        performed_at=utcnow(),
    )
    session.add(validation)
    if outcome == ValidationOutcome.PASS and case.status == CaseStatus.PENDING_VERIFICATION:
        transition_case(
            session, case, CaseStatus.VERIFIED, validator_user.id, reason_code="VALIDATION_PASS"
        )
    return validation


# ── Duplicados (nunca merge automático) ────────────────────────────────


def _pair_exists(session: Session, a, b) -> DuplicateCandidate | None:
    return session.execute(
        sa.select(DuplicateCandidate).where(
            DuplicateCandidate.entity_type == DuplicateEntityType.CASE,
            DuplicateCandidate.algorithm_version == ALGORITHM_VERSION,
            sa.or_(
                sa.and_(DuplicateCandidate.left_id == a, DuplicateCandidate.right_id == b),
                sa.and_(DuplicateCandidate.left_id == b, DuplicateCandidate.right_id == a),
            ),
        )
    ).scalar_one_or_none()


def find_duplicate_candidates(session: Session, case: Case) -> list[DuplicateCandidate]:
    """Heurística v1 de candidatos a duplicado; solo propone, nunca fusiona."""
    scored: dict[Any, tuple[float, str]] = {}

    # 1) Mismo hogar en otro caso.
    if case.household_id is not None:
        rows = session.execute(
            sa.select(Case.id).where(
                Case.household_id == case.household_id, Case.id != case.id
            )
        ).scalars()
        for other_id in rows:
            scored[other_id] = (0.9, "same_household")

    # 2) Persona AFECTADA compartida con otro caso del mismo incidente.
    affected = sa.select(CasePerson.person_id).where(
        CasePerson.case_id == case.id, CasePerson.role == sa.literal("AFFECTED")
    )
    rows = session.execute(
        sa.select(CasePerson.case_id)
        .join(Case, Case.id == CasePerson.case_id)
        .where(
            CasePerson.person_id.in_(affected),
            CasePerson.role == sa.literal("AFFECTED"),
            CasePerson.case_id != case.id,
            Case.incident_id == case.incident_id,
        )
    ).scalars()
    for other_id in rows:
        if other_id not in scored:
            scored[other_id] = (0.85, "shared_affected_person")

    # 3) Mismo municipio (admin2 del report) + mismo tamaño de hogar.
    my_admin2 = session.execute(
        sa.select(Location.admin2)
        .join(Report, Report.location_id == Location.id)
        .join(ReportSubject, ReportSubject.report_id == Report.id)
        .where(ReportSubject.case_id == case.id, Location.admin2.is_not(None))
        .limit(1)
    ).scalar_one_or_none()
    my_members = None
    if case.household_id is not None:
        my_members = session.execute(
            sa.select(Household.member_count).where(Household.id == case.household_id)
        ).scalar_one_or_none()
    if my_admin2 is not None and my_members is not None:
        rows = session.execute(
            sa.select(ReportSubject.case_id)
            .join(Report, Report.id == ReportSubject.report_id)
            .join(Location, Location.id == Report.location_id)
            .join(Case, Case.id == ReportSubject.case_id)
            .join(Household, Household.id == Case.household_id)
            .where(
                Location.admin2 == my_admin2,
                Household.member_count == my_members,
                ReportSubject.case_id.is_not(None),
                ReportSubject.case_id != case.id,
                Case.incident_id == case.incident_id,
            )
        ).scalars()
        for other_id in rows:
            if other_id not in scored:
                scored[other_id] = (0.6, "same_admin2_and_household_size")

    candidates: list[DuplicateCandidate] = []
    for other_id, (score, feature) in scored.items():
        existing = _pair_exists(session, case.id, other_id)
        if existing is not None:
            candidates.append(existing)
            continue
        left, right = sorted([case.id, other_id])
        candidate = DuplicateCandidate(
            entity_type=DuplicateEntityType.CASE,
            left_id=left,
            right_id=right,
            algorithm_version=ALGORITHM_VERSION,
            similarity_score=score,
            matched_features={"heuristic": feature},
            status=DuplicateStatus.PROPOSED,
        )
        session.add(candidate)
        candidates.append(candidate)
    session.flush()
    return candidates


def merge_cases(
    session: Session,
    survivor: Case,
    duplicate: Case,
    actor_user_id,
    reason: str,
) -> Case:
    """Fusión manual: reapunta hijos al sobreviviente; nada se borra."""
    if survivor.id == duplicate.id:
        raise ValueError("Un caso no puede fusionarse consigo mismo")

    # La transición valida ANTES de mover hijos (falla → nada cambia).
    transition_case(
        session,
        duplicate,
        CaseStatus.DUPLICATE,
        actor_user_id,
        reason_code="MERGED_INTO:" + survivor.case_code,
        note=reason,
    )

    # ReportSubjects: reapuntar salvo que el reporte ya apunte al survivor.
    subjects = session.execute(
        sa.select(ReportSubject).where(ReportSubject.case_id == duplicate.id)
    ).scalars().all()
    for subject in subjects:
        clash = session.execute(
            sa.select(ReportSubject.id).where(
                ReportSubject.report_id == subject.report_id,
                ReportSubject.case_id == survivor.id,
                ReportSubject.subject_type == subject.subject_type,
            )
        ).first()
        if clash is None:
            subject.case_id = survivor.id

    # CasePersons: reapuntar salvo duplicado exacto persona+rol en survivor.
    case_persons = session.execute(
        sa.select(CasePerson).where(CasePerson.case_id == duplicate.id)
    ).scalars().all()
    for cp in case_persons:
        clash = session.execute(
            sa.select(CasePerson.id).where(
                CasePerson.case_id == survivor.id,
                CasePerson.person_id == cp.person_id,
                CasePerson.role == cp.role,
            )
        ).first()
        if clash is None:
            cp.case_id = survivor.id

    # Needs: todas al survivor.
    session.execute(
        sa.update(Need).where(Need.case_id == duplicate.id).values(case_id=survivor.id)
    )

    # Candidato de duplicado correspondiente → MERGED.
    candidate = _pair_exists(session, survivor.id, duplicate.id)
    if candidate is not None:
        candidate.status = DuplicateStatus.MERGED
        candidate.survivor_id = survivor.id
        candidate.decided_by = actor_user_id
        candidate.decided_at = utcnow()

    publish(
        session,
        event_type="case.merged",
        aggregate_type="case",
        aggregate_id=survivor.id,
        payload={
            "survivor_case_code": survivor.case_code,
            "duplicate_case_code": duplicate.case_code,
        },
    )
    return survivor


# ── Resumen sin PII ────────────────────────────────────────────────────


def case_summary(session: Session, case: Case) -> dict:
    """Resumen operativo del caso SIN PII (PRIV-01): nada de narrativa,
    teléfonos, nombres ni coordenadas."""
    needs = session.execute(
        sa.select(Need, NeedCatalog.code)
        .join(NeedCatalog, NeedCatalog.id == Need.catalog_id)
        .where(Need.case_id == case.id)
        .order_by(Need.created_at)
    ).all()
    persons_count = session.execute(
        sa.select(sa.func.count()).select_from(CasePerson).where(CasePerson.case_id == case.id)
    ).scalar()
    reports_count = session.execute(
        sa.select(sa.func.count())
        .select_from(ReportSubject)
        .where(ReportSubject.case_id == case.id)
    ).scalar()
    return {
        "id": str(case.id),
        "case_code": case.case_code,
        "status": case.status.value,
        "priority_band": case.priority_band.value if case.priority_band else None,
        "needs": [
            {
                "id": str(need.id),
                "status": need.status.value,
                "requested_qty": _num(need.requested_qty),
                "covered_qty": _num(need.covered_qty),
                "catalog_code": code,
            }
            for need, code in needs
        ],
        "persons_count": int(persons_count or 0),
        "reports_count": int(reports_count or 0),
    }
