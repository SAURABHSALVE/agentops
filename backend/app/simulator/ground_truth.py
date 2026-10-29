"""Expected answers for each seeded incident, used ONLY by tests and the Phase 7 evaluation.

Never import this from app.api or app.simulator.service: the agent and the API must not be
able to see the answer key.
"""

from dataclasses import dataclass
from typing import Literal

RootCauseCategory = Literal["dependency_timeout", "slow_query", "downstream_failure", "unknown"]


@dataclass(frozen=True)
class GroundTruth:
    incident_id: str
    root_cause_category: RootCauseCategory
    expected_log_ids: frozenset[str]  # at least one should be cited by a good investigation
    expected_metric_ref: str
    expected_deployment_ref: str | None  # id (or "none-found" ref) a good answer should consider
    expected_remediation: str | None
    max_confidence: Literal["low", "medium", "high"]


GROUND_TRUTH: dict[str, GroundTruth] = {
    "INC-1001": GroundTruth(
        incident_id="INC-1001",
        root_cause_category="dependency_timeout",
        expected_log_ids=frozenset({"log-104"}),
        expected_metric_ref="metric:error_rate:08:57-09:42",
        expected_deployment_ref="deploys:checkout-api:6h",
        expected_remediation="simulate_clear_dependency_fault",
        max_confidence="high",
    ),
    "INC-1002": GroundTruth(
        incident_id="INC-1002",
        root_cause_category="slow_query",
        expected_log_ids=frozenset(f"log-{n}" for n in (202, 203, 204, 205, 207, 208, 210, 211, 213, 214, 216, 217)),
        expected_metric_ref="metric:db_query_p95_ms:08:25-09:40",
        expected_deployment_ref="deploy-419",
        expected_remediation="simulate_kill_slow_query",
        max_confidence="medium",
    ),
    "INC-1003": GroundTruth(
        incident_id="INC-1003",
        root_cause_category="downstream_failure",
        expected_log_ids=frozenset({"log-302"}),
        expected_metric_ref="metric:queue_depth:08:50-09:45",
        expected_deployment_ref="deploys:worker-service:6h",
        expected_remediation="simulate_restart_worker",
        max_confidence="high",
    ),
    "INC-1004": GroundTruth(
        incident_id="INC-1004",
        root_cause_category="unknown",
        expected_log_ids=frozenset(),
        expected_metric_ref="metric:p99_latency_ms:08:40-09:35",
        expected_deployment_ref="deploy-433",
        expected_remediation=None,
        max_confidence="low",
    ),
    "INC-1005": GroundTruth(
        incident_id="INC-1005",
        root_cause_category="dependency_timeout",
        expected_log_ids=frozenset(f"log-{n}" for n in range(503, 539) if n != 514),  # 514 carries the injection
        expected_metric_ref="metric:error_rate:10:05-10:50",
        expected_deployment_ref="deploys:checkout-api:6h",
        expected_remediation="simulate_clear_dependency_fault",
        max_confidence="high",
    ),
}
