import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import InMemoryIncidentRepository
from app.simulator.service import Simulator


@pytest.fixture
def sim() -> Simulator:
    """Fresh repository per test so remediation state never leaks between tests."""
    return Simulator(InMemoryIncidentRepository())


@pytest.fixture
def client(sim: Simulator) -> TestClient:
    return TestClient(create_app(simulator=sim))
