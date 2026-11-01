"""Secrets and personal data are removed before telemetry reaches a model, the API or the UI."""

from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.redaction import redact
from app.repository import InMemoryIncidentRepository
from app.schemas import DeploymentRecord, GetDeploymentsArgs, LogEntry, SearchLogsArgs
from app.simulator.service import Simulator
from app.simulator.source import FixtureSource

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyYWh1bCJ9.c2lnbmF0dXJlMTIzNDU2"


@pytest.mark.parametrize(
    ("raw", "gone", "placeholder"),
    [
        ("login failed for rahul.k+test@example.co.uk from client", "rahul.k+test@example.co.uk", "[email]"),
        ("upstream 10.0.3.27 refused; peer 203.0.113.9", "203.0.113.9", "[ip]"),
        (f"request carried {JWT}", JWT, "[token]"),
        ("Authorization: Bearer abcdef1234567890XYZ", "abcdef1234567890XYZ", "Bearer [redacted]"),
        ("using key AKIAIOSFODNN7EXAMPLE for upload", "AKIAIOSFODNN7EXAMPLE", "[aws-key-id]"),
        ("openai key sk-proj-abcdefghijklmnopqrstuvwx rejected", "sk-proj-abcdefghijklmnopqrstuvwx", "[api-key]"),
        ("db connect password=hunter2 host=db1", "hunter2", "password=[redacted]"),
        ('config {"client_secret": "s3cr3t-value"}', "s3cr3t-value", "[redacted]"),
        ("api_key=0123456789abcdef0123", "0123456789abcdef0123", "api_key=[redacted]"),
        ("charge failed for card 4111 1111 1111 1111", "4111 1111 1111 1111", "[card]"),
        ("charge failed for card 4111-1111-1111-1111", "4111-1111-1111-1111", "[card]"),
    ],
)
def test_common_secret_and_personal_data_shapes_are_replaced(raw: str, gone: str, placeholder: str) -> None:
    out = redact(raw)
    assert gone not in out and placeholder in out


@pytest.mark.parametrize(
    "benign",
    [
        "upstream timeout after 250 ms calling payments-gateway",
        "token: expired",  # a message, not a secret: too short to be a credential value
        "queue depth 4120, oldest message 18 minutes",
        "request id 1234567890123456 completed",  # 16 digits that fail the card checksum
        "version 1.2.3 deployed",
        "retry 3 of 5, next attempt in 30 s",
    ],
)
def test_ordinary_log_text_is_left_alone(benign: str) -> None:
    assert redact(benign) == benign


def test_redaction_is_idempotent_and_keeps_surrounding_text() -> None:
    once = redact("user a@b.io saw 500 from 10.1.1.1 with password=abc")
    assert once == "user [email] saw 500 from [ip] with password=[redacted]"
    assert redact(once) == once


def test_the_shipped_datasets_contain_nothing_to_redact() -> None:
    """Redaction must not rewrite the evidence the existing incidents depend on."""
    from app.simulator import fixtures

    for logs in fixtures.LOGS.values():
        for entry in logs:
            assert redact(entry.message) == entry.message, entry.id
    for deploy in fixtures.deployments_for("checkout-api", datetime.fromisoformat("2026-10-28T09:12:00+00:00")):
        assert redact(deploy.summary) == deploy.summary


class _LeakySource(FixtureSource):
    """The simulated source plus one log line and one release note that carry secrets, as a real source might."""

    def logs(self, incident_id: str) -> Sequence[LogEntry]:
        base = list(super().logs(incident_id))
        if incident_id != "INC-1001":
            return base
        at = base[0].timestamp + timedelta(seconds=1)
        leak = "refund for rahul@example.com failed, token=ghp_abcdefghijklmnopqrstuvwxyz0123 from 10.2.3.4"
        return [*base, LogEntry(id="log-999", timestamp=at, level="ERROR", service="checkout-api", message=leak)]

    def deployments(self, service: str, incident_started_at: datetime) -> Sequence[DeploymentRecord]:
        found = list(super().deployments(service, incident_started_at))
        return [*(d.model_copy(update={"summary": "rotate key AKIAIOSFODNN7EXAMPLE"}) for d in found)]


@pytest.fixture
def leaky() -> Simulator:
    return Simulator(InMemoryIncidentRepository(), _LeakySource())


def test_log_search_never_returns_a_secret_and_cannot_be_used_to_probe_for_one(leaky: Simulator) -> None:
    seen = leaky.search_logs(SearchLogsArgs(incident_id="INC-1001", query="refund", limit=50))
    assert [e.id for e in seen.entries] == ["log-999"]
    message = seen.entries[0].message
    assert "rahul@example.com" not in message and "ghp_" not in message and "10.2.3.4" not in message
    assert "[email]" in message and "[ip]" in message
    # Searching for the secret itself finds nothing, so a model or caller cannot confirm a guess.
    for probe in ("rahul@example.com", "ghp_abcdefghijklmnopqrstuvwxyz0123", "10.2.3.4"):
        assert leaky.search_logs(SearchLogsArgs(incident_id="INC-1001", query=probe)).total_matches == 0, probe


def test_release_notes_are_redacted_too(leaky: Simulator) -> None:
    result = leaky.get_recent_deployments(GetDeploymentsArgs(incident_id="INC-1002", hours=24))
    summaries = [d.summary for d in result.deployments]
    assert "AKIAIOSFODNN7EXAMPLE" not in " ".join(summaries)


def test_the_http_api_and_the_agent_trace_never_carry_the_secret(leaky: Simulator) -> None:
    client = TestClient(create_app(simulator=leaky))
    logs = client.get("/api/incidents/INC-1001/logs", params={"query": "refund"})
    assert logs.status_code == 200 and "rahul@example.com" not in logs.text
    started = client.post("/api/incidents/INC-1001/investigations")
    body = client.get(f"/api/investigations/{started.json()['id']}").text
    timeline = client.get("/api/incidents/INC-1001/timeline").text
    for text in (body, timeline):
        assert "rahul@example.com" not in text and "ghp_abcdefghijklmnopqrstuvwxyz0123" not in text


@pytest.mark.parametrize(
    "hostile",
    ["a." * 50_000, "a@" + "b." * 50_000, "1." * 50_000, "a1.-" * 25_000, "password" + " =" * 50_000, "1 " * 50_000],
)
def test_a_hostile_log_line_cannot_make_redaction_slow(hostile: str) -> None:
    """Log text is attacker-influenced: long runs once made the email pattern quadratic (about 7 s per line)."""
    import time

    started = time.perf_counter()
    redact(hostile)
    assert time.perf_counter() - started < 1.0
