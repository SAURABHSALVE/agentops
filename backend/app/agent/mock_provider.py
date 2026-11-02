"""Deterministic, offline stand-in for a model. It is NOT an LLM and is labelled that way.

It is a small rule-based policy over the tool results already in the conversation: pick the next
tools from the incident's headline signals, probe logs for known failure signatures, then write
a conclusion from what came back. It makes no network call, needs no credentials, and never sees
the evaluation answer key. Free text from logs is never copied into its output: observations are
built from structured fields (counts, ids, metric numbers), so injected text cannot be echoed.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from app.agent.calibration import CAUSE_WINDOW_MINUTES, SUSTAINED_FRACTION
from app.agent.models import ProviderInfo, TokenUsage
from app.agent.prompts import decode_tool_result
from app.agent.provider import CompletionRequest, CompletionResponse, Message, ToolCallRequest

# Headline-signal label -> metric to inspect.
_SIGNAL_METRICS: tuple[tuple[str, str], ...] = (
    ("5xx", "error_rate"),
    ("error rate", "error_rate"),
    ("p99", "p99_latency_ms"),
    ("p95", "p95_latency_ms"),
    ("queue", "queue_depth"),
)

# Log probes run in parallel on the first investigation round: (query, level).
_PROBES: tuple[tuple[str, str], ...] = (("timeout", "ERROR"), ("slow query", "WARN"), ("503", "ERROR"))
_SWEEPS: tuple[tuple[str, str], ...] = (("e", "ERROR"), ("e", "WARN"))  # broad, used when probes find nothing


@dataclass(frozen=True)
class _Signature:
    key: str
    pattern: re.Pattern[str]
    cause: str  # "{x}" is filled from the first capture group
    action_id: str


_SIGNATURES: tuple[_Signature, ...] = (
    _Signature(
        "dependency_timeout",
        re.compile(r"upstream timeout calling ([\w.-]+)"),
        "Requests are timing out calling the upstream dependency '{x}'.",
        "simulate_clear_dependency_fault",
    ),
    _Signature(
        "slow_query",
        re.compile(r"slow query \d+ms: (\w+)"),
        "A slow database query ('{x}') is degrading response times.",
        "simulate_kill_slow_query",
    ),
    _Signature(
        "downstream_failure",
        re.compile(r"downstream ([\w.-]+) returned 5\d\d"),
        "The downstream service '{x}' is failing and retries are piling up.",
        "simulate_restart_worker",
    ),
)


def _calls(*calls: tuple[str, dict[str, Any]]) -> CompletionResponse:
    return CompletionResponse(
        tool_calls=tuple(
            ToolCallRequest(id=f"mock-{i}", name=n, arguments=json.dumps(a)) for i, (n, a) in enumerate(calls, 1)
        )
    )


def _results(messages: tuple[Message, ...]) -> list[tuple[str, dict[str, Any]]]:
    """Tool results so far as (tool name, payload), in order. Name comes from the envelope header."""
    out: list[tuple[str, dict[str, Any]]] = []
    for m in messages:
        if m.role != "tool":
            continue
        header = m.content.split("\n", 1)[0]
        match = re.search(r'tool="([^"]+)"', header)
        out.append((match.group(1) if match else "", decode_tool_result(m.content)))
    return out


class MockProvider:
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(kind="mock", label="Mock provider: deterministic rule-based policy, not an LLM")

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        results = _results(request.messages)
        used = {name for name, _ in results}

        if not results:
            return _calls(("get_incident", {}))

        if used == {"get_incident"}:
            incident = results[0][1]
            metrics: list[str] = []
            for signal in incident.get("signals", []):
                label = str(signal.get("label", "")).lower()
                for needle, metric in _SIGNAL_METRICS:
                    if needle in label and metric not in metrics:
                        metrics.append(metric)
            return _calls(
                ("get_recent_deployments", {"hours": 6}),
                *(("get_service_metrics", {"metric": m}) for m in metrics),
                *(("search_logs", {"query": q, "level": lv, "limit": 20}) for q, lv in _PROBES),
            )

        logs = [p for n, p in results if n == "search_logs" and "entries" in p]
        # Track what was *asked*, not what succeeded, so a failing call is never retried forever.
        asked = [(c.name, json.loads(c.arguments)) for m in request.messages for c in m.tool_calls]
        swept = any(n == "search_logs" and a.get("query") == "e" for n, a in asked)
        asked_db_metric = any(n == "get_service_metrics" and a.get("metric") == "db_query_p95_ms" for n, a in asked)
        follow_ups: list[tuple[str, dict[str, Any]]] = []
        if not any(p["total_matches"] for p in logs) and not swept:
            follow_ups += [("search_logs", {"query": q, "level": lv, "limit": 20}) for q, lv in _SWEEPS]
        if any("query" in e["message"].lower() for p in logs for e in p["entries"]) and not asked_db_metric:
            follow_ups.append(("get_service_metrics", {"metric": "db_query_p95_ms"}))
        if follow_ups:
            return _calls(*follow_ups)

        return CompletionResponse(final_text=json.dumps(self._conclude(results)), usage=TokenUsage())

    # ---- conclusion -------------------------------------------------------------------------

    def _conclude(self, results: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        incident = next((p for n, p in results if n == "get_incident" and "service" in p), {})
        service = incident.get("service", "the affected service")
        entries = [e for n, p in results if n == "search_logs" and "entries" in p for e in p["entries"]]
        metrics = [p for n, p in results if n == "get_service_metrics" and "summary" in p]
        deploys = next((p for n, p in results if n == "get_recent_deployments" and "deployments" in p), None)

        best: tuple[_Signature, list[dict[str, Any]], str] | None = None
        for sig in _SIGNATURES:
            hits = [(e, hit.group(1)) for e in entries if (hit := sig.pattern.search(e["message"]))]
            if len({e["id"] for e, _ in hits}) >= 3 and (best is None or len(hits) > len(best[1])):
                best = (sig, [e for e, _ in hits], hits[0][1])

        sustained = [m for m in metrics if m["summary"]["elevated_fraction"] >= SUSTAINED_FRACTION]
        evidence: list[dict[str, str]] = []
        limitations = [
            "Based on simulated demo telemetry from the allowlisted read-only tools, not production data.",
            "This was produced by a deterministic rule-based mock, not a language model.",
        ]

        if deploys:
            recent = [
                d
                for d in deploys["deployments"]
                if d["change_type"] != "docs" and 0 <= d["minutes_before_incident_start"] <= CAUSE_WINDOW_MINUTES
            ]
            docs = [d for d in deploys["deployments"] if d["change_type"] == "docs"]
        else:
            recent, docs = [], []

        for met in sorted(sustained or metrics, key=lambda x: -x["summary"]["elevated_fraction"])[:2]:
            s = met["summary"]
            evidence.append(
                {
                    "source": "metrics",
                    "reference_id": met["reference_ids"][0],
                    "observation": (
                        f"{met['metric']} baseline {s['baseline']:g} {met['unit']}, peak {s['peak']:g}; elevated for "
                        f"{round(s['elevated_fraction'] * 100)}% of the window."
                    ),
                }
            )

        if best is None or not sustained:
            for d in docs[:1]:
                evidence.append(
                    {
                        "source": "deployments",
                        "reference_id": d["id"],
                        "observation": "A docs-only change shortly before the incident; no runtime effect expected.",
                    }
                )
            if deploys and not deploys["deployments"]:
                evidence.append(
                    {
                        "source": "deployments",
                        "reference_id": deploys["reference_ids"][0],
                        "observation": f"No deployments to {service} in the last {deploys['hours']} hours.",
                    }
                )
            return {
                "summary": f"Insufficient evidence to identify a root cause for the incident on {service}.",
                "root_cause_hypothesis": "Undetermined: the logs and metrics do not agree on one sustained cause.",
                "confidence": "low",
                "confidence_rationale": (
                    "No repeated error pattern backed by a sustained, incident-long metric elevation was found, "
                    "so the evidence does not support a specific cause."
                ),
                "evidence": evidence,
                "alternative_hypotheses": [
                    "Intermittent client-side or network effects not visible in these logs.",
                    "A short-lived resource contention that the sampled metrics only partly capture.",
                ],
                "recommended_next_step": (
                    "Gather more telemetry (traces, per-endpoint latency) and re-run the investigation "
                    "before taking any action."
                ),
                "remediation_action_id": None,
                "limitations": limitations,
            }

        sig, matched, subject = best
        ids = sorted({e["id"] for e in matched})
        cited = [ids[0], ids[len(ids) // 2], ids[-1]]
        evidence = [
            {
                "source": "logs",
                "reference_id": ref,
                "observation": f"One of {len(ids)} log entries matching the '{sig.key}' pattern for '{subject}'.",
            }
            for ref in dict.fromkeys(cited)
        ] + evidence

        if recent:
            for d in recent:
                evidence.append(
                    {
                        "source": "deployments",
                        "reference_id": d["id"],
                        "observation": (
                            f"{d['change_type']} change deployed {d['minutes_before_incident_start']} minutes "
                            "before the incident started."
                        ),
                    }
                )
            confidence, rationale = (
                "medium",
                "Logs and metrics agree, but a recent change could also have contributed and cannot be separated out.",
            )
            alternatives = [f"The recent change ({recent[0]['id']}) may be a contributing or primary cause."]
        else:
            if deploys:
                evidence.append(
                    {
                        "source": "deployments",
                        "reference_id": (
                            deploys["deployments"][0]["id"] if deploys["deployments"] else deploys["reference_ids"][0]
                        ),
                        "observation": "No recent code, config or dependency change near the incident start.",
                    }
                )
            confidence, rationale = (
                "high",
                "A repeated failure signature in the logs matches a sustained metric elevation that began with "
                "the incident, and no recent change competes as an explanation.",
            )
            alternatives = ["The dependency or service may itself be degraded for an upstream reason not visible here."]

        return {
            "summary": f"{sig.cause.format(x=subject)} Metrics confirm a sustained elevation on {service}.",
            "root_cause_hypothesis": sig.cause.format(x=subject),
            "confidence": confidence,
            "confidence_rationale": rationale,
            "evidence": evidence[:12],
            "alternative_hypotheses": alternatives,
            "recommended_next_step": "Approve the proposed simulated remediation, then verify the metrics recover.",
            "remediation_action_id": sig.action_id,
            "limitations": limitations,
        }
