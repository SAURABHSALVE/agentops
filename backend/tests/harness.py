"""Run the real coordinator against the real simulator with a chosen provider."""

from datetime import UTC, datetime

from app.agent.coordinator import Coordinator, Limits
from app.agent.models import Investigation, ProviderInfo
from app.agent.provider import LLMProvider
from app.agent.tools import ToolRunner
from app.schemas import GetIncidentArgs
from app.simulator.service import Simulator

FIXED_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def run_investigation(
    sim: Simulator,
    provider: LLMProvider,
    incident_id: str = "INC-1005",
    *,
    max_rounds: int = 6,
    max_total_tokens: int = 20_000,
    max_tool_calls: int = 12,
    max_calls_per_round: int = 6,
    tool_timeout: float = 5,
    max_result_bytes: int = 16_384,
) -> tuple[Investigation, list[Investigation]]:
    """Return (final investigation, every intermediate snapshot published while it ran)."""
    runner = ToolRunner(sim, timeout_seconds=tool_timeout, max_result_bytes=max_result_bytes)
    limits = Limits(
        max_rounds=max_rounds,
        max_total_tokens=max_total_tokens,
        max_output_tokens=1_500,
        max_tool_calls=max_tool_calls,
        max_calls_per_round=max_calls_per_round,
    )
    initial = Investigation(
        id="inv-0001",
        incident_id=incident_id,
        status="running",
        provider=ProviderInfo(kind="mock", label="test"),
        started_at=FIXED_NOW,
    )
    published: list[Investigation] = []
    service = sim.get_incident(GetIncidentArgs(incident_id=incident_id)).service
    final = Coordinator(provider, runner, limits, lambda: FIXED_NOW).run(initial, service, published.append)
    return final, published
