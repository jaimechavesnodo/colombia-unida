import pytest

from app.core.state_machines import (
    MACHINES,
    InvalidTransition,
    assert_transition,
    is_terminal,
)


def test_happy_paths_are_valid():
    paths = {
        "report": ["DRAFT", "COLLECTING", "SUBMITTED", "LINKED"],
        "case": ["DRAFT", "INCOMPLETE", "PENDING_VERIFICATION", "VERIFIED", "ACTIVE",
                 "PARTIALLY_SERVED", "SERVED", "CLOSED"],
        "need": ["REPORTED", "PENDING_VERIFICATION", "VERIFIED", "OPEN", "PARTIALLY_COVERED",
                 "COVERED", "IN_TRANSIT", "DELIVERED_PENDING_VERIFY", "DELIVERED_VERIFIED",
                 "CLOSED"],
        "offer": ["DRAFT", "PENDING_CONFIRMATION", "AVAILABLE", "PARTIALLY_ALLOCATED",
                  "FULLY_ALLOCATED"],
        "allocation": ["DRAFT", "RESERVED", "DONOR_CONFIRMED", "ACCEPTED_BY_OPERATOR",
                       "READY_FOR_FULFILLMENT", "IN_FULFILLMENT", "FULFILLED"],
        "shipment": ["PLANNED", "READY", "PICKED_UP", "IN_TRANSIT", "ARRIVED", "DELIVERED"],
        "delivery": ["SCHEDULED", "ARRIVING", "HANDED_OVER_PENDING_PROOF", "PROOF_SUBMITTED",
                     "VERIFIED"],
        "public_profile": ["DRAFT", "PENDING_REVIEW", "APPROVED", "PUBLISHED"],
    }
    for entity, path in paths.items():
        for src, dst in zip(path, path[1:], strict=False):
            assert_transition(entity, src, dst)


def test_invalid_transition_raises():
    with pytest.raises(InvalidTransition):
        assert_transition("case", "DRAFT", "CLOSED")
    with pytest.raises(InvalidTransition):
        assert_transition("need", "REPORTED", "DELIVERED_VERIFIED")
    with pytest.raises(InvalidTransition):
        assert_transition("allocation", "RESERVED", "FULFILLED")


def test_terminal_states_have_no_exits():
    # DISPUTED en delivery no es terminal; REJECTED sí
    assert is_terminal("delivery", "REJECTED")
    assert not is_terminal("delivery", "DISPUTED")
    assert is_terminal("case", "CLOSED")
    assert is_terminal("report", "DUPLICATE")


def test_all_destinations_exist_as_states():
    for entity, machine in MACHINES.items():
        for src, dests in machine.items():
            for d in dests:
                assert d in machine, f"{entity}: {src} → {d} apunta a estado no definido"


def test_unknown_state_raises():
    with pytest.raises(InvalidTransition):
        assert_transition("case", "NOPE", "ACTIVE")
