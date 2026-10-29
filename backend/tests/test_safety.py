"""Structural guards: no code path to a shell, no answer-key leakage, swappable storage."""

import re
from pathlib import Path

from app.errors import IncidentNotFoundError
from app.repository import IncidentRepository
from app.schemas import (
    GetDeploymentsArgs,
    GetIncidentArgs,
    IncidentDetail,
    IncidentSummary,
    SearchLogsArgs,
)
from app.simulator.fixtures import INCIDENTS
from app.simulator.service import Simulator

APP_DIR = Path(__file__).resolve().parent.parent / "app"
# `re.compile(` is legitimate; only the bare builtin compile() is forbidden.
FORBIDDEN = re.compile(
    r"\b(subprocess|os\.system|os\.popen|os\.exec\w*|pty|ctypes|__import__)\b|\beval\(|\bexec\(|(?<![\w.])compile\("
)


def test_app_code_has_no_shell_or_dynamic_execution_primitives() -> None:
    offenders = [
        f"{p.relative_to(APP_DIR)}:{n}: {line.strip()}"
        for p in APP_DIR.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if FORBIDDEN.search(line)
    ]
    assert offenders == []


IMPORTS_GROUND_TRUTH = re.compile(r"^\s*(from|import)\s+\S*ground_truth|^\s*from\s+\S+\s+import\s+.*ground_truth", re.M)


def test_answer_key_is_only_imported_by_tests() -> None:
    importers = [
        p.relative_to(APP_DIR).as_posix()
        for p in APP_DIR.rglob("*.py")
        if IMPORTS_GROUND_TRUTH.search(p.read_text(encoding="utf-8"))
    ]
    assert importers == []


class DictRepository:
    """A deliberately different storage implementation, to prove the Protocol is enough."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[IncidentDetail, list[str]]] = {i.id: (i, []) for i in INCIDENTS}

    def list_incidents(self) -> list[IncidentSummary]:
        fields = set(IncidentSummary.model_fields)
        return [IncidentSummary.model_validate(d.model_dump(include=fields)) for d, _ in self._rows.values()]

    def get_incident(self, incident_id: str) -> IncidentDetail:
        if incident_id not in self._rows:
            raise IncidentNotFoundError("Incident not found.")
        return self._rows[incident_id][0]

    def get_applied_remediation(self, incident_id: str) -> str | None:
        applied = self._rows[incident_id][1]
        return applied[-1] if applied else None

    def record_remediation(self, incident_id: str, action_id: str) -> bool:
        applied = self._rows[incident_id][1]
        if applied and applied[-1] == action_id:
            return False
        applied.append(action_id)
        return True


def test_simulator_works_with_any_repository_implementing_the_protocol() -> None:
    repo: IncidentRepository = DictRepository()  # mypy checks the Protocol is satisfied
    sim = Simulator(repo)
    assert len(sim.list_incidents()) == 5
    assert sim.get_incident(GetIncidentArgs(incident_id="INC-1003")).service == "worker-service"
    assert sim.search_logs(SearchLogsArgs(incident_id="INC-1003", query="503", limit=1)).returned == 1
    assert sim.get_recent_deployments(GetDeploymentsArgs(incident_id="INC-1002")).deployments
    assert sim.execute_remediation("INC-1003", "simulate_restart_worker").status == "executed"
    assert sim.verify_resolution("INC-1003").status == "resolved"
