"""Checks the plain sentences of an answer against what the tools returned in the same run.

The grounding check proves that every cited id was retrieved, and the confidence ceiling checks the numbers. Neither
reads the prose, and prose is where a model most often slips: it can say "no deployments" in one sentence after the
deployments tool returned two dozen changes. A model cannot be trusted to reread its own answer, so the backend does a
narrow check of its own.

It is deliberately narrow. It only flags a sentence that states, in so many words, that there were *no* deployments or
changes, when the deployments tool of this run returned at least one that the sentence's own scope (code or config, a
time window) covers. Anything hedged ("no single change explains it", "no relevant change") is left alone: it is a
judgement, not a contradiction. A wrong flag costs a retry, a missed one costs nothing new, so doubt means no flag.
Problem texts are built here from counts and minutes only, never from tool text, because they are shown to the user and
sent back to the model.
"""

import re
from collections.abc import Iterable, Sequence
from typing import Any

# "no deployments", "no recent code changes", "without any releases", "did not find any changes", "no evidence of X"
_NO = re.compile(
    r"\b(?:no|zero|without(?:\s+any|\s+a|\s+an)?|(?:did|do|does|could|can)\s*(?:not|n't)\s+(?:find|see|detect|observe)"
    r"(?:\s+any)?)\s+((?:[\w'-]+\s+){0,3}?)(deployments?|deploys?|changes?|releases?|rollouts?)\b"
    # ...but "no recent change competes as an explanation" is about cause, not about whether changes exist.
    r"(?!\s+(?:[\w'-]+\s+){0,3}?(?:compet|explain|account|caus|correlat|relat|appear|seem|stand|match|coincid|link|"
    r"align|point|fit))",
    re.IGNORECASE,
)
# "no deployments were found", "changes were not detected"
_NOT_FOUND = re.compile(
    r"\b(deployments?|deploys?|changes?|releases?|rollouts?)\b[^.;:]{0,30}?"
    r"\b(?:was|were|are|is|has been|have been)\s+(?:not|never)\s+"
    r"(?:found|detected|observed|seen|made|recorded|reported)\b",
    re.IGNORECASE,
)
# Words that turn "no X" into a judgement about which change matters, not a statement that none exist.
_HEDGES = frozenset(
    "single specific particular clear clearly obvious definitive apparent direct one confirmed significant major "
    "notable relevant unusual suspicious likely other another new additional further".split()
)
_WINDOW = re.compile(
    r"\b(?:last|past|previous|preceding|prior|within|in\s+the|before)\s+(?:the\s+)?(\d+(?:\.\d+)?)\s*"
    r"(minutes?|mins?|m|hours?|hrs?|h)\b",
    re.IGNORECASE,
)
_LAST_HOUR = re.compile(r"\b(?:last|past|previous|preceding)\s+hour\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+")


def _changes(observed: Sequence[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, payload in observed:
        if name == "get_recent_deployments":
            out.extend(d for d in payload.get("deployments", []) if d.get("change_type") != "docs")
    return out


def _window_minutes(sentence: str) -> float | None:
    if _LAST_HOUR.search(sentence):
        return 60.0
    match = _WINDOW.search(sentence)
    if match is None:
        return None
    amount = float(match.group(1))
    return amount * 60 if match.group(2).lower().startswith("h") else amount


def _claims_no_changes(sentence: str) -> bool:
    for match in _NO.finditer(sentence):
        if not _HEDGES.intersection(w.lower() for w in match.group(1).split()):
            return True
    return _NOT_FOUND.search(sentence) is not None


def deployment_contradictions(text: str, observed: Sequence[tuple[str, dict[str, Any]]]) -> list[str]:
    """Problems for each sentence of `text` that says there were no changes when the deployments tool returned some."""
    changes = _changes(observed)
    if not changes:
        return []
    problems: list[str] = []
    for sentence in _SENTENCES.split(text):
        if not _claims_no_changes(sentence):
            continue
        pool = changes
        lowered = sentence.lower()
        if "code" in lowered:
            pool = [c for c in pool if c.get("change_type") == "code"]
        elif "config" in lowered:
            pool = [c for c in pool if c.get("change_type") == "config"]
        window = _window_minutes(sentence)
        if window is not None:
            pool = [c for c in pool if abs(int(c.get("minutes_before_incident_start", 10**9))) <= window]
        if not pool:
            continue
        closest = min(abs(int(c.get("minutes_before_incident_start", 0))) for c in pool)
        problems.append(
            f"it says there were no deployments or changes, but the deployments check returned {len(pool)} "
            f"that fit that wording, the closest {closest} minute(s) from when the alarm began"
        )
    return problems


def claim_problems(texts: Iterable[str], observed: Sequence[tuple[str, dict[str, Any]]]) -> list[str]:
    """Every distinct contradiction between the sentences in `texts` and the tool results in `observed`."""
    seen: list[str] = []
    for text in texts:
        for problem in deployment_contradictions(text, observed):
            if problem not in seen:
                seen.append(problem)
    return seen[:3]
