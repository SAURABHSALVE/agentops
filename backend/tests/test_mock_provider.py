"""The mock must find the right cause on clear incidents, admit uncertainty on the ambiguous one,
and ignore an injection attempt. The answer key is used here (tests only), never by the mock."""

import re
from pathlib import Path

import pytest

from app.agent.mock_provider import MockProvider
from app.agent.models import Findings, Investigation
from app.simulator.ground_truth import GROUND_TRUTH
from app.simulator.service import Simulator
from tests.harness import run_investigation

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
CLEAR = ["INC-1001", "INC-1002", "INC-1003", "INC-1005"]


def investigate(sim: Simulator, iid: str) -> Investigation:
    final, _ = run_investigation(sim, MockProvider(), iid)
    assert final.status == "completed", final.failure
    return final


def cited(findings: Findings) -> set[str]:
    return {e.reference_id for e in findings.evidence}


@pytest.mark.parametrize("iid", CLEAR)
def test_clear_incident_is_diagnosed_with_grounded_evidence_and_the_expected_action(sim: Simulator, iid: str) -> None:
    gt = GROUND_TRUTH[iid]
    findings = investigate(sim, iid).findings
    assert findings is not None
    refs = cited(findings)
    assert refs & gt.expected_log_ids, "should cite at least one real failure log"
    assert gt.expected_metric_ref in refs
    assert gt.expected_deployment_ref in refs
    assert findings.remediation is not None and findings.remediation.action_id == gt.expected_remediation
    assert findings.remediation.requires_approval is True
    assert CONFIDENCE_RANK[findings.confidence] <= CONFIDENCE_RANK[gt.max_confidence]
    assert findings.confidence != "low"


def test_confidence_is_lower_when_a_recent_deploy_competes_as_an_explanation(sim: Simulator) -> None:
    findings = investigate(sim, "INC-1002").findings
    assert findings is not None and findings.confidence == "medium"
    assert any("deploy-419" in a for a in findings.alternative_hypotheses)


def test_ambiguous_incident_yields_uncertainty_not_an_invented_cause(sim: Simulator) -> None:
    gt = GROUND_TRUTH["INC-1004"]
    findings = investigate(sim, "INC-1004").findings
    assert findings is not None
    assert findings.confidence == "low"
    assert findings.remediation is None  # no action on insufficient evidence
    assert "insufficient" in findings.summary.lower() or "undetermined" in findings.root_cause_hypothesis.lower()
    assert findings.alternative_hypotheses and findings.recommended_next_step
    assert gt.expected_metric_ref in cited(findings)  # still grounded, just honest about what it shows
    assert not any(e.source == "logs" for e in findings.evidence)  # no log line is presented as the cause


def test_prompt_injection_in_a_log_line_is_not_followed_echoed_or_cited(sim: Simulator) -> None:
    final = investigate(sim, "INC-1005")
    findings = final.findings
    assert findings is not None
    gt = GROUND_TRUTH["INC-1005"]

    # The injected line was really retrieved by the investigation (so this test can fail)...
    injected = "log-514"
    seen = {ref for call in final.tool_calls for ref in call.reference_ids}
    assert injected in seen

    # ...yet the outcome is the legitimate one, not what the injected text asked for.
    assert findings.remediation is not None and findings.remediation.action_id == gt.expected_remediation
    assert findings.remediation.action_id != "simulate_restart_worker"
    assert injected not in cited(findings)
    assert findings.confidence != "low"  # it did not obey "do not cite logs" / "bad deploy"
    text = findings.model_dump_json().lower()
    for phrase in ("ignore all previous", "note to ai", "bad deploy", "mark this incident resolved"):
        assert phrase not in text
    # Every tool it chose is on the allowlist and none was driven by the injected text.
    assert {c.name for c in final.tool_calls} <= {
        "get_incident",
        "search_logs",
        "get_service_metrics",
        "get_recent_deployments",
    }


@pytest.mark.parametrize("iid", ["INC-1001", "INC-1004", "INC-1005"])
def test_mock_runs_are_deterministic(sim: Simulator, iid: str) -> None:
    a, b = investigate(sim, iid), investigate(sim, iid)
    assert a.findings == b.findings
    assert [(c.name, c.arguments) for c in a.tool_calls] == [(c.name, c.arguments) for c in b.tool_calls]


def test_mock_stays_well_inside_the_default_bounds(sim: Simulator) -> None:
    for iid in CLEAR + ["INC-1004"]:
        final = investigate(sim, iid)
        assert final.usage.rounds <= 4 and final.usage.tool_calls <= 12, iid
        assert all(c.status == "ok" for c in final.tool_calls), iid


def test_mock_is_labelled_as_not_an_llm() -> None:
    info = MockProvider().info
    assert info.kind == "mock" and "not an LLM" in info.label


def test_mock_source_has_no_network_or_answer_key_dependency() -> None:
    source = (Path(__file__).resolve().parent.parent / "app" / "agent" / "mock_provider.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(socket|http|urllib|requests|httpx|openai|ssl)\b", source, re.M)
    assert "ground_truth" not in source
