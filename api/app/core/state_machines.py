"""Máquinas de estado del alcance (§8.2).

Toda transición se valida en backend (§8.1); la UI no escribe status
arbitrariamente. Este módulo es la fuente única de transiciones válidas.
Los estados terminales solo se reabren con comando específico + motivo
+ auditoría (lo aplica la capa de comandos, no esta tabla).
"""

from enum import Enum

# Cada entrada: estado_origen -> conjunto de estados destino permitidos.
# Camino feliz + excepciones documentadas en el alcance.

REPORT_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"COLLECTING", "WITHDRAWN"},
    "COLLECTING": {"SUBMITTED", "SUBMITTED_INCOMPLETE", "WITHDRAWN"},
    "SUBMITTED_INCOMPLETE": {"SUBMITTED", "LINKED", "DUPLICATE", "WITHDRAWN", "REJECTED"},
    "SUBMITTED": {"LINKED", "DUPLICATE", "WITHDRAWN", "REJECTED"},
    "LINKED": {"DUPLICATE", "WITHDRAWN"},
    "DUPLICATE": set(),
    "WITHDRAWN": set(),
    "REJECTED": set(),
}

CASE_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"INCOMPLETE", "PENDING_VERIFICATION", "ON_HOLD", "DUPLICATE", "CANCELLED"},
    "INCOMPLETE": {"PENDING_VERIFICATION", "ON_HOLD", "DUPLICATE", "REJECTED", "CANCELLED",
                   "SUSPICIOUS"},
    "PENDING_VERIFICATION": {"VERIFIED", "INCOMPLETE", "ON_HOLD", "DUPLICATE", "REJECTED",
                             "CANCELLED", "SUSPICIOUS"},
    "VERIFIED": {"ACTIVE", "ON_HOLD", "SUSPICIOUS", "CANCELLED"},
    "ACTIVE": {"PARTIALLY_SERVED", "SERVED", "ON_HOLD", "SUSPICIOUS", "CANCELLED"},
    "PARTIALLY_SERVED": {"SERVED", "ACTIVE", "ON_HOLD", "SUSPICIOUS", "CANCELLED"},
    "SERVED": {"CLOSED", "ACTIVE"},
    "ON_HOLD": {"DRAFT", "INCOMPLETE", "PENDING_VERIFICATION", "VERIFIED", "ACTIVE", "CANCELLED"},
    "SUSPICIOUS": {"PENDING_VERIFICATION", "VERIFIED", "ACTIVE", "REJECTED", "CANCELLED"},
    "CLOSED": set(),
    "DUPLICATE": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}

NEED_TRANSITIONS: dict[str, set[str]] = {
    "REPORTED": {"NEEDS_CLARIFICATION", "PENDING_VERIFICATION", "DUPLICATE", "REJECTED",
                 "CANCELLED"},
    "NEEDS_CLARIFICATION": {"REPORTED", "PENDING_VERIFICATION", "REJECTED", "CANCELLED"},
    "PENDING_VERIFICATION": {"VERIFIED", "NEEDS_CLARIFICATION", "DUPLICATE", "REJECTED",
                             "CANCELLED"},
    "VERIFIED": {"OPEN", "PUBLICABLE", "NEEDS_CLARIFICATION", "CANCELLED"},
    "PUBLICABLE": {"OPEN", "CANCELLED"},
    "OPEN": {"PARTIALLY_COVERED", "COVERED", "NEEDS_CLARIFICATION", "CANCELLED"},
    "PARTIALLY_COVERED": {"COVERED", "OPEN", "IN_TRANSIT", "CANCELLED"},
    "COVERED": {"IN_TRANSIT", "OPEN", "CANCELLED"},
    "IN_TRANSIT": {"DELIVERED_PENDING_VERIFY", "PARTIALLY_COVERED", "OPEN"},
    "DELIVERED_PENDING_VERIFY": {"DELIVERED_VERIFIED", "IN_TRANSIT", "OPEN"},
    "DELIVERED_VERIFIED": {"CLOSED", "OPEN"},
    "CLOSED": set(),
    "REJECTED": set(),
    "DUPLICATE": set(),
    "CANCELLED": set(),
}

OFFER_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_CONFIRMATION", "WITHDRAWN"},
    "PENDING_CONFIRMATION": {"AVAILABLE", "WITHDRAWN", "BLOCKED", "EXPIRED"},
    "AVAILABLE": {"PARTIALLY_ALLOCATED", "FULLY_ALLOCATED", "EXPIRED", "WITHDRAWN", "BLOCKED"},
    "PARTIALLY_ALLOCATED": {"AVAILABLE", "FULLY_ALLOCATED", "EXPIRED", "WITHDRAWN", "BLOCKED"},
    "FULLY_ALLOCATED": {"PARTIALLY_ALLOCATED", "AVAILABLE"},
    "EXPIRED": set(),
    "WITHDRAWN": set(),
    "BLOCKED": {"AVAILABLE", "WITHDRAWN"},
}

MATCH_TRANSITIONS: dict[str, set[str]] = {
    "PROPOSED": {"REVIEW_REQUIRED", "APPROVED", "REJECTED", "EXPIRED", "SUPERSEDED"},
    "REVIEW_REQUIRED": {"APPROVED", "REJECTED", "EXPIRED", "SUPERSEDED"},
    "APPROVED": {"SUPERSEDED"},
    "REJECTED": set(),
    "EXPIRED": set(),
    "SUPERSEDED": set(),
}

ALLOCATION_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"RESERVED", "CANCELLED"},
    "RESERVED": {"DONOR_CONFIRMED", "EXPIRED", "CANCELLED"},
    "DONOR_CONFIRMED": {"ACCEPTED_BY_OPERATOR", "EXPIRED", "CANCELLED", "DISPUTED"},
    "ACCEPTED_BY_OPERATOR": {"READY_FOR_FULFILLMENT", "CANCELLED", "DISPUTED"},
    "READY_FOR_FULFILLMENT": {"IN_FULFILLMENT", "CANCELLED", "DISPUTED"},
    "IN_FULFILLMENT": {"FULFILLED", "PARTIALLY_FULFILLED", "DISPUTED", "CANCELLED"},
    "PARTIALLY_FULFILLED": {"FULFILLED", "IN_FULFILLMENT", "DISPUTED", "CANCELLED"},
    "FULFILLED": {"DISPUTED"},
    "EXPIRED": set(),
    "CANCELLED": set(),
    "DISPUTED": {"IN_FULFILLMENT", "FULFILLED", "CANCELLED"},
}

SHIPMENT_TRANSITIONS: dict[str, set[str]] = {
    "PLANNED": {"READY", "CANCELLED"},
    "READY": {"PICKED_UP", "DELAYED", "CANCELLED"},
    "PICKED_UP": {"IN_TRANSIT", "DELAYED", "LOST", "CANCELLED"},
    "IN_TRANSIT": {"ARRIVED", "DELAYED", "PARTIALLY_DELIVERED", "LOST"},
    "DELAYED": {"IN_TRANSIT", "PICKED_UP", "READY", "LOST", "CANCELLED"},
    "ARRIVED": {"DELIVERED", "PARTIALLY_DELIVERED"},
    "PARTIALLY_DELIVERED": {"DELIVERED", "IN_TRANSIT"},
    "DELIVERED": set(),
    "LOST": set(),
    "CANCELLED": set(),
}

DELIVERY_TRANSITIONS: dict[str, set[str]] = {
    "SCHEDULED": {"ARRIVING", "FAILED", "CANCELLED"},
    "ARRIVING": {"HANDED_OVER_PENDING_PROOF", "FAILED", "CANCELLED"},
    "HANDED_OVER_PENDING_PROOF": {"PROOF_SUBMITTED", "PARTIAL", "REJECTED", "FAILED"},
    "PROOF_SUBMITTED": {"VERIFIED", "PARTIAL", "DISPUTED", "REJECTED"},
    "PARTIAL": {"PROOF_SUBMITTED", "VERIFIED", "DISPUTED"},
    "VERIFIED": {"DISPUTED"},
    "REJECTED": set(),
    "DISPUTED": {"VERIFIED", "FAILED"},
    "FAILED": set(),
    "CANCELLED": set(),
}

PUBLIC_PROFILE_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_REVIEW", "ARCHIVED"},
    "PENDING_REVIEW": {"APPROVED", "DRAFT", "ARCHIVED"},
    "APPROVED": {"PUBLISHED", "DRAFT", "ARCHIVED"},
    "PUBLISHED": {"PAUSED", "WITHDRAWN", "ARCHIVED"},
    "PAUSED": {"PUBLISHED", "WITHDRAWN", "ARCHIVED"},
    "WITHDRAWN": {"ARCHIVED"},
    "ARCHIVED": set(),
}

MACHINES: dict[str, dict[str, set[str]]] = {
    "report": REPORT_TRANSITIONS,
    "case": CASE_TRANSITIONS,
    "need": NEED_TRANSITIONS,
    "offer": OFFER_TRANSITIONS,
    "match": MATCH_TRANSITIONS,
    "allocation": ALLOCATION_TRANSITIONS,
    "shipment": SHIPMENT_TRANSITIONS,
    "delivery": DELIVERY_TRANSITIONS,
    "public_profile": PUBLIC_PROFILE_TRANSITIONS,
}


class InvalidTransition(Exception):
    def __init__(self, entity: str, from_status: str, to_status: str):
        self.entity, self.from_status, self.to_status = entity, from_status, to_status
        super().__init__(f"Transición inválida de {entity}: {from_status} → {to_status}")


def _as_str(status: str | Enum) -> str:
    return status.value if isinstance(status, Enum) else status


def assert_transition(entity: str, from_status: str | Enum, to_status: str | Enum) -> None:
    """Lanza InvalidTransition si el cambio no está permitido."""
    machine = MACHINES[entity]
    src, dst = _as_str(from_status), _as_str(to_status)
    if src not in machine:
        raise InvalidTransition(entity, src, dst)
    if dst not in machine.get(src, set()):
        raise InvalidTransition(entity, src, dst)


def is_terminal(entity: str, status: str | Enum) -> bool:
    return not MACHINES[entity].get(_as_str(status), set())
