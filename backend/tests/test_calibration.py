"""Confidence is capped by the evidence the run retrieved, whatever a provider claims; the cap only ever lowers it."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.calibration import CAUSE_WINDOW_MINUTES, evidence_ceiling
from app.agent.models import Investigation
from app.main import create_app
from app.schemas import GetDeploymentsArgs, GetMetricsArgs, SearchLogsArgs
from app.simulator.service import Simulator
from tests.harness import run_investigation
from tests.scripted_provider import ScriptedProvider, call, calls, final


def metric(fraction: float) -> tuple[str, dict[str, Any]]:
    return ("get_service_metrics", {"summary": {"elevated_fraction": fraction}})


def deploys(*changes: tuple[str, int]) -> tuple[str, dict[str, Any]]:
    return (
        "get_recent_deployments",
        {"deployments": [{"change_type": t, "minutes_before_incident_start": m} for t, m in changes]},
    )


# ---- the rules -------------------------------------------------------------------------------------


def test_nothing_looked_at_supports_only_low() -> None:
    ceiling = evidence_ceiling([("search_logs", {"entries": []})])
    assert ceiling.level == "low" and ceiling.reason == "no metric was checked in this run"


def test_brief_or_intermittent_metrics_support_only_low() -> None:
    ceiling = evidence_ceiling([metric(0.146), metric(0.0), deploys()])
    assert ceiling.level == "low" and "half of the incident window" in str(ceiling.reason)


def test_a_sustained_metric_with_no_competing_change_supports_high() -> None:
    assert evidence_ceiling([metric(0.98), deploys()]).level == "high"
    assert evidence_ceiling([metric(0.5), deploys(("docs", 5))]).level == "high"  # exactly half counts; docs do not


def test_any_one_sustained_metric_is_enough_even_beside_a_quiet_one() -> None:
    assert evidence_ceiling([metric(0.0), metric(1.0), deploys()]).level == "high"


def test_a_recent_runtime_change_caps_at_medium() -> None:
    assert evidence_ceiling([metric(1.0), deploys(("code", 12))]).level == "medium"
    assert evidence_ceiling([metric(1.0), deploys(("config", CAUSE_WINDOW_MINUTES))]).level == "medium"
    assert evidence_ceiling([metric(1.0), deploys(("code", CAUSE_WINDOW_MINUTES + 1))]).level == "high"  # long ago


def test_not_having_checked_deployments_caps_at_medium() -> None:
    ceiling = evidence_ceiling([metric(1.0)])
    assert ceiling.level == "medium" and "not checked" in str(ceiling.reason)


def test_lowers_only_reports_what_is_actually_above_the_ceiling() -> None:
    low = evidence_ceiling([])
    assert low.lowers("medium") and low.lowers("high") and not low.lowers("low")


# ---- through the coordinator -----------------------------------------------------------------------


def _ref(value: str) -> dict[str, str]:
    kind = value.split(":")[0]
    source = {"metric": "metrics", "deploys": "deployments", "deploy-419": "deployments"}.get(kind, "logs")
    return {"source": source, "reference_id": value, "observation": "Retrieved this run."}


def investigate(sim: Simulator, incident: str, steps: list[Any], **answer: Any) -> Investigation:
    script = [*steps, final(**answer)]
    done, _ = run_investigation(sim, ScriptedProvider(*script), incident)
    assert done.status == "completed", done.failure
    return done


def test_a_claim_of_medium_on_intermittent_metrics_is_lowered_to_low_and_says_so(sim: Simulator) -> None:
    ref = sim.get_service_metrics(GetMetricsArgs(incident_id="INC-1004", metric="p99_latency_ms")).reference_ids[0]
    done = investigate(
        sim,
        "INC-1004",
        [calls(call("get_service_metrics", {"metric": "p99_latency_ms"}), call("get_recent_deployments", {}, "c2"))],
        confidence="medium",
        confidence_rationale="One cache warm-up warning looks related.",
        evidence=[_ref(ref)],
    )
    f = done.findings
    assert f is not None and f.confidence == "low" and f.confidence_lowered_from == "medium"
    assert f.confidence_rationale.startswith("One cache warm-up warning looks related.")
    assert "Lowered from medium to low by a system check" in f.confidence_rationale


def test_a_fix_proposed_at_a_confidence_that_falls_to_low_is_withheld_not_a_failed_run(sim: Simulator) -> None:
    # The fix is allowed for this incident's service, but the run never looked at a metric, so only low is supported.
    log_ref = sim.search_logs(SearchLogsArgs(incident_id="INC-1005", query="timeout", limit=3)).entries[0].id
    done = investigate(
        sim,
        "INC-1005",
        [calls(call("search_logs", {"query": "timeout", "limit": 3}))],
        confidence="medium",
        evidence=[_ref(log_ref)],
        remediation_action_id="simulate_clear_dependency_fault",
    )
    assert done.findings is not None
    assert done.findings.confidence == "low" and done.findings.remediation is None
    assert any("withheld" in x for x in done.findings.limitations)
    assert done.remediation_state is None  # nothing to approve


def test_an_off_list_fix_still_fails_the_run_even_when_the_confidence_would_be_lowered(sim: Simulator) -> None:
    ref = sim.get_service_metrics(GetMetricsArgs(incident_id="INC-1004", metric="p99_latency_ms")).reference_ids[0]
    provider = ScriptedProvider(
        calls(call("get_service_metrics", {"metric": "p99_latency_ms"})),
        final(confidence="medium", evidence=[_ref(ref)], remediation_action_id="rm_rf_everything"),
    )
    done, _ = run_investigation(sim, provider, "INC-1004")
    assert done.status == "failed" and done.failure is not None and done.failure.code == "remediation_not_allowed"


def test_high_is_lowered_to_medium_when_a_recent_change_could_explain_it(sim: Simulator) -> None:
    metric_ref = sim.get_service_metrics(
        GetMetricsArgs(incident_id="INC-1002", metric="db_query_p95_ms")
    ).reference_ids[0]
    deploy = sim.get_recent_deployments(GetDeploymentsArgs(incident_id="INC-1002", hours=6)).deployments[0].id
    done = investigate(
        sim,
        "INC-1002",
        [calls(call("get_service_metrics", {"metric": "db_query_p95_ms"}), call("get_recent_deployments", {}, "c2"))],
        confidence="high",
        evidence=[_ref(metric_ref), {"source": "deployments", "reference_id": deploy, "observation": "A change."}],
    )
    assert done.findings is not None
    assert done.findings.confidence == "medium" and done.findings.confidence_lowered_from == "high"


def test_a_supported_answer_is_left_exactly_as_the_provider_gave_it(sim: Simulator) -> None:
    metric_ref = sim.get_service_metrics(GetMetricsArgs(incident_id="INC-1005", metric="error_rate")).reference_ids[0]
    log_ref = sim.search_logs(SearchLogsArgs(incident_id="INC-1005", query="timeout", limit=3)).entries[0].id
    done = investigate(
        sim,
        "INC-1005",
        [
            calls(
                call("get_service_metrics", {"metric": "error_rate"}),
                call("get_recent_deployments", {}, "c2"),
                call("search_logs", {"query": "timeout", "limit": 3}, "c3"),
            )
        ],
        confidence="high",
        confidence_rationale="Original words.",
        evidence=[_ref(metric_ref), _ref(log_ref)],
        remediation_action_id="simulate_clear_dependency_fault",
    )
    f = done.findings
    assert f is not None and f.confidence == "high" and f.confidence_lowered_from is None
    assert f.confidence_rationale == "Original words." and f.remediation is not None


def test_the_ceiling_never_raises_a_cautious_answer(sim: Simulator) -> None:
    metric_ref = sim.get_service_metrics(GetMetricsArgs(incident_id="INC-1005", metric="error_rate")).reference_ids[0]
    done = investigate(
        sim,
        "INC-1005",
        [calls(call("get_service_metrics", {"metric": "error_rate"}), call("get_recent_deployments", {}, "c2"))],
        confidence="low",
        evidence=[_ref(metric_ref)],
    )
    assert done.findings is not None
    assert done.findings.confidence == "low" and done.findings.confidence_lowered_from is None


def test_a_failed_tool_call_does_not_count_as_evidence(sim: Simulator) -> None:
    log_ref = sim.search_logs(SearchLogsArgs(incident_id="INC-1005", query="timeout", limit=3)).entries[0].id
    done = investigate(
        sim,
        "INC-1005",
        [
            calls(
                call("search_logs", {"query": "timeout", "limit": 3}),
                call("get_service_metrics", {"metric": "nope"}, "c2"),
            )
        ],
        confidence="medium",
        evidence=[_ref(log_ref)],
    )
    assert done.findings is not None
    assert done.findings.confidence == "low" and "no metric was checked" in done.findings.confidence_rationale


# ---- the offline mock agrees with the ceiling ------------------------------------------------------


@pytest.mark.parametrize("incident", ["INC-1001", "INC-1002", "INC-1003", "INC-1004", "INC-1005", "INC-1006"])
def test_the_mock_is_never_lowered_on_any_simulated_incident(client: TestClient, incident: str) -> None:
    started = client.post(f"/api/incidents/{incident}/investigations")
    inv = client.get(f"/api/investigations/{started.json()['id']}").json()
    assert inv["status"] == "completed"
    assert inv["findings"]["confidence_lowered_from"] is None, inv["findings"]["confidence_rationale"]


def test_the_mock_is_never_lowered_on_the_captured_incidents() -> None:
    from pathlib import Path

    from app.config import Settings
    from app.simulator.captured import CapturedSource

    dataset = Path(__file__).resolve().parent.parent / "data" / "captured" / "lab-capture.json"
    CapturedSource.load(dataset)  # fails loudly if the dataset moved
    import dataclasses

    client = TestClient(
        create_app(settings=dataclasses.replace(Settings(), storage_mode="memory", telemetry_dataset=str(dataset)))
    )
    for incident in ("INC-2001", "INC-2002", "INC-2003"):
        started = client.post(f"/api/incidents/{incident}/investigations")
        inv = client.get(f"/api/investigations/{started.json()['id']}").json()
        assert inv["status"] == "completed"
        assert inv["findings"]["confidence_lowered_from"] is None, (incident, inv["findings"]["confidence_rationale"])
