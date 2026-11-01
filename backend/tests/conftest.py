import os

# Tests must never read the real backend/.env, call a live model, or write a local database.
os.environ["AGENTOPS_NO_DOTENV"] = "1"
os.environ["STORAGE_MODE"] = "memory"
os.environ["AGENT_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

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
