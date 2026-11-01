"""Builds the stacks once per stage, with a tiny stand-in backend so tests do not run pip for every case."""

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from agentops_infra.backend_stack import BackendStack
from agentops_infra.config import StageConfig
from agentops_infra.data_stack import DataStack
from agentops_infra.frontend_stack import FrontendStack
from agentops_infra.github_oidc_stack import GitHubOidcStack
from agentops_infra.monitoring_stack import MonitoringStack

ACCOUNT = "123456789012"
ENV = cdk.Environment(account=ACCOUNT, region="us-east-1")
REPO = "SAURABHSALVE/agentops"
SUBJECT_PREFIX = "repo:SAURABHSALVE@90780335/agentops@1437085899"  # what GitHub signs for this repository
EMAIL = "alerts@example.com"
PLACEHOLDER = Path(__file__).resolve().parent.parent / "placeholder_site"


@dataclass
class Built:
    cfg: StageConfig
    data: Template
    backend: Template
    frontend: Template
    monitoring: Template
    backend_stack: BackendStack


def build(stage: str, backend_dir: Path, agent_provider: str = "mock") -> Built:
    app = cdk.App()
    cfg = StageConfig(stage=stage, alert_email=EMAIL, budget_limit_usd=10, agent_provider=agent_provider)
    data = DataStack(app, "Data", cfg=cfg, env=ENV)
    backend = BackendStack(app, "Backend", cfg=cfg, data=data, backend_dir=backend_dir, env=ENV)
    frontend = FrontendStack(
        app, "Frontend", backend=backend, site_dir=PLACEHOLDER, placeholder_dir=PLACEHOLDER, env=ENV
    )
    monitoring = MonitoringStack(app, "Monitoring", cfg=cfg, backend=backend, env=ENV)
    return Built(
        cfg,
        Template.from_stack(data),
        Template.from_stack(backend),
        Template.from_stack(frontend),
        Template.from_stack(monitoring),
        backend,
    )


@pytest.fixture(scope="session")
def fake_backend(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("backend")
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "requirements-lambda.txt").write_text("", encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def dev(fake_backend: Path) -> Built:
    return build("dev", fake_backend)


@pytest.fixture(scope="session")
def prod(fake_backend: Path) -> Built:
    return build("prod", fake_backend)


@pytest.fixture(scope="session")
def oidc() -> Template:
    app = cdk.App()
    return Template.from_stack(GitHubOidcStack(app, "Oidc", subject_prefix=SUBJECT_PREFIX, env=ENV))


@pytest.fixture(scope="session")
def live(fake_backend: Path) -> Built:
    return build("dev", fake_backend, agent_provider="openai")
