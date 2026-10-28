"""Tool arguments are validated server-side and outputs are bounded."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas import (
    GetDeploymentsArgs,
    GetIncidentArgs,
    GetMetricsArgs,
    LogEntry,
    SearchLogsArgs,
)
from app.simulator import fixtures
from app.simulator.service import MAX_MESSAGE_CHARS, Simulator

BAD_LOG_ARGS: list[dict[str, Any]] = [
    {"query": ""},
    {"query": "   "},
    {"query": "x" * 101},
    {"query": "ok", "limit": 0},
    {"query": "ok", "limit": 51},
    {"query": "ok", "level": "TRACE"},
    {"query": "ok", "shell": "ls -la"},  # unknown argument names are rejected
    {"query": "ok", "incident_id": "INC-1"},
    {"query": "ok", "incident_id": "../etc/passwd"},
    {"query": "ok", "incident_id": "INC-1001; DROP TABLE x"},
    {"query": "ok", "incident_id": "inc-1001"},
]


@pytest.mark.parametrize("overrides", BAD_LOG_ARGS)
def test_search_logs_rejects_invalid_arguments(overrides: dict[str, Any]) -> None:
    payload: dict[str, Any] = {"incident_id": "INC-1001", **overrides}
    with pytest.raises(ValidationError):
        SearchLogsArgs(**payload)


def test_search_logs_defaults_and_whitespace_stripping() -> None:
    args = SearchLogsArgs(incident_id="INC-1001", query="  timeout  ")
    assert args.query == "timeout"
    assert args.limit == 20
    assert args.level is None


@pytest.mark.parametrize("metric", ["cpu", "", "error_rate; rm -rf /", "ERROR_RATE"])
def test_metrics_rejects_unknown_metric(metric: str) -> None:
    with pytest.raises(ValidationError):
        GetMetricsArgs(incident_id="INC-1001", metric=metric)


@pytest.mark.parametrize("hours", [0, -1, 25, 1000])
def test_deployments_rejects_out_of_range_hours(hours: int) -> None:
    with pytest.raises(ValidationError):
        GetDeploymentsArgs(incident_id="INC-1001", hours=hours)


def test_get_incident_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        GetIncidentArgs(incident_id="INC-1001", verbose=True)  # type: ignore[call-arg]


def test_log_search_is_truncated_to_limit(sim: Simulator) -> None:
    res = sim.search_logs(SearchLogsArgs(incident_id="INC-1003", query="notifier", limit=5))
    assert res.returned == 5 == len(res.entries)
    assert res.total_matches == 60
    assert res.truncated is True


def test_log_messages_are_capped_in_length(sim: Simulator, monkeypatch: pytest.MonkeyPatch) -> None:
    long_entry = LogEntry(
        id="log-100",
        timestamp=datetime(2026, 10, 28, 9, 13, tzinfo=UTC),
        level="ERROR",
        service="checkout-api",
        message="timeout " + "A" * 5000,
    )
    monkeypatch.setitem(fixtures.LOGS, "INC-1001", (long_entry,))
    res = sim.search_logs(SearchLogsArgs(incident_id="INC-1001", query="timeout"))
    assert len(res.entries[0].message) == MAX_MESSAGE_CHARS


def test_log_search_only_returns_entries_inside_the_incident_window(sim: Simulator) -> None:
    res = sim.search_logs(SearchLogsArgs(incident_id="INC-1001", query="health", limit=50))
    assert res.entries  # sanity
    assert all(res.window_start <= e.timestamp <= res.window_end for e in res.entries)
