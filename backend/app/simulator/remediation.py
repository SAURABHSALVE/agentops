"""The complete, closed set of remediation actions. Each only flips simulated state.

There is deliberately no code path from an action id to a shell, SQL, IAM, deployment or
infrastructure call: an action id is only ever used as a dictionary key.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class RemediationSpec:
    action_id: str
    description: str
    expected_effect: str
    applicable_services: frozenset[str]


REMEDIATIONS: Mapping[str, RemediationSpec] = MappingProxyType(
    {
        "simulate_clear_dependency_fault": RemediationSpec(
            action_id="simulate_clear_dependency_fault",
            description="Clear the injected timeout fault on the simulated payments dependency.",
            expected_effect="Dependency latency returns to baseline and checkout 5xx rate falls below 1%.",
            applicable_services=frozenset({"checkout-api"}),
        ),
        "simulate_kill_slow_query": RemediationSpec(
            action_id="simulate_kill_slow_query",
            description="Terminate the long-running simulated query and reset the connection pool.",
            expected_effect="Query p95 returns toward baseline; the underlying index fix still needs follow-up.",
            applicable_services=frozenset({"catalog-api"}),
        ),
        "simulate_restart_worker": RemediationSpec(
            action_id="simulate_restart_worker",
            description="Reset the simulated worker state and drain stuck retries.",
            expected_effect="Queue depth begins to fall once the simulated downstream recovers.",
            applicable_services=frozenset({"worker-service"}),
        ),
    }
)
