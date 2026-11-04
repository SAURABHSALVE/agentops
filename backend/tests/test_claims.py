"""The check that an answer's sentences do not contradict the tool results of the same run.

It is narrow on purpose: it flags only a sentence that says there were no deployments or changes when the deployments
tool returned some that the sentence's own wording covers. Anything hedged is left alone, because a wrong flag costs a
retry and a missed one costs nothing new."""

import json
from typing import Any

import pytest

from app.agent.claims import claim_problems
from app.agent.coordinator import _Contradicted, validate_answer
from app.agent.models import EvidenceSource


def deploys(*changes: tuple[str, int]) -> list[tuple[str, dict[str, Any]]]:
    """A deployments result with (change_type, minutes before the incident) changes."""
    return [
        (
            "get_recent_deployments",
            {
                "deployments": [
                    {"id": f"ct:{i}", "change_type": kind, "minutes_before_incident_start": minutes}
                    for i, (kind, minutes) in enumerate(changes)
                ]
            },
        )
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "There were no deployments or changes before the alarm.",
        "No recent changes were found in the account.",
        "No Lambda deployments happened around that time.",
        "The account shows no recent configuration changes.",
        "We did not find any changes.",
        "Without any recent deployment, the cause is probably load.",
        "Deployments were not found in the window.",
        "There is no evidence of recent changes.",
    ],
)
def test_a_flat_denial_is_flagged_when_changes_were_returned(sentence: str) -> None:
    assert claim_problems([sentence], deploys(("config", 30)))


@pytest.mark.parametrize(
    "sentence",
    [
        "No single change explains the errors.",
        "There is no specific deployment to blame.",
        "No relevant changes stand out.",
        "No clear code change caused this.",
        "The error rate is not related to any change.",
        "The logs show no errors.",
        "A deployment 20 minutes earlier could explain it.",
        "No further changes were made after that.",
        "A repeated failure matches the metric, and no recent change competes as an explanation.",
        "No deployment explains the sudden rise.",
        "No change accounts for the extra errors.",
    ],
)
def test_a_hedged_or_unrelated_sentence_is_never_flagged(sentence: str) -> None:
    assert claim_problems([sentence], deploys(("config", 30), ("code", 5))) == []


def test_nothing_is_flagged_when_no_change_was_returned() -> None:
    assert claim_problems(["There were no deployments or changes."], deploys()) == []
    assert claim_problems(["There were no deployments or changes."], []) == []  # the tool was never called


def test_a_docs_change_is_not_a_deployment() -> None:
    assert claim_problems(["There were no deployments."], deploys(("docs", 5))) == []


def test_the_kind_named_in_the_sentence_has_to_exist() -> None:
    only_config = deploys(("config", 30))
    assert claim_problems(["There were no code changes."], only_config) == []
    assert claim_problems(["There were no configuration changes."], only_config)
    assert claim_problems(["There were no code changes."], deploys(("code", 30)))


def test_a_time_window_in_the_sentence_limits_which_changes_count() -> None:
    far = deploys(("config", 240))
    assert claim_problems(["No changes were made in the last 30 minutes."], far) == []
    assert claim_problems(["No changes were made in the last 6 hours."], far)
    assert claim_problems(["No changes in the past hour."], deploys(("code", 12)))
    assert claim_problems(["No changes in the past hour."], far) == []


def test_the_problem_text_holds_counts_and_minutes_only() -> None:
    (problem,) = claim_problems(["No changes were found."], deploys(("config", 30), ("code", 7)))
    assert "2 that fit" in problem and "7 minute" in problem


def test_the_same_problem_is_reported_once() -> None:
    got = claim_problems(["No changes were found.", "No changes were found."], deploys(("config", 30)))
    assert len(got) == 1


# ---- in the coordinator's answer check ----------------------------------------------------------------------


def answer(summary: str) -> str:
    return json.dumps(
        {
            "summary": summary,
            "root_cause_hypothesis": "Load.",
            "confidence": "high",
            "confidence_rationale": "Metric and logs agree.",
            "evidence": [{"source": "metrics", "reference_id": "metric:x", "observation": "Up."}],
            "alternative_hypotheses": [],
            "recommended_next_step": "Check capacity.",
            "remediation_action_id": None,
            "limitations": [],
        }
    )


GROUNDED: dict[str, EvidenceSource] = {"metric:x": "metrics"}


def test_validation_sends_a_contradiction_back_when_a_retry_is_possible() -> None:
    with pytest.raises(_Contradicted) as caught:
        validate_answer(
            answer("There were no deployments."),
            GROUNDED,
            "svc",
            observed=deploys(("config", 30)),
            retry_contradictions=True,
        )
    assert len(caught.value.problems) == 1


def test_validation_keeps_the_answer_but_flags_it_and_caps_confidence_when_no_retry_is_left() -> None:
    findings = validate_answer(
        answer("There were no deployments."), GROUNDED, "svc", observed=deploys(("config", 30)), live=True
    )
    assert findings.confidence == "medium" and findings.confidence_lowered_from == "high"
    assert any(lim.startswith("Check this against the evidence") for lim in findings.limitations)
    assert "contradicts what the tools returned" in findings.confidence_rationale


def test_a_truthful_answer_is_untouched() -> None:
    findings = validate_answer(
        answer("A config change 30 minutes earlier may explain it."), GROUNDED, "svc", observed=deploys(("config", 30))
    )
    assert findings.confidence == "high" and findings.confidence_lowered_from is None
