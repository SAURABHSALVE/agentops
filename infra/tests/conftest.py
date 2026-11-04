"""Builds the stacks once per stage, with a tiny stand-in backend so tests do not run pip for every case."""

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from agentops_infra.auth_stack import AuthStack
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
    auth: Template | None = None  # None when built with auth="none"


def build(
    stage: str,
    backend_dir: Path,
    agent_provider: str = "mock",
    auth: str = "cognito",
    aws_connect: bool = False,
    connect_accounts: tuple[str, ...] = (),
    require_access_code: bool = True,
) -> Built:
    app = cdk.App()
    cfg = StageConfig(
        stage=stage,
        alert_email=EMAIL,
        budget_limit_usd=10,
        agent_provider=agent_provider,
        auth=auth,
        aws_connect=aws_connect,
        connect_accounts=connect_accounts,
        require_access_code=require_access_code,
    )
    data = DataStack(app, "Data", cfg=cfg, env=ENV)
    auth_stack = AuthStack(app, "Auth", cfg=cfg, env=ENV) if cfg.sign_in else None
    backend = BackendStack(app, "Backend", cfg=cfg, data=data, auth=auth_stack, backend_dir=backend_dir, env=ENV)
    frontend = FrontendStack(
        app,
        "Frontend",
        cfg=cfg,
        backend=backend,
        auth=auth_stack,
        site_dir=PLACEHOLDER,
        placeholder_dir=PLACEHOLDER,
        env=ENV,
    )
    monitoring = MonitoringStack(app, "Monitoring", cfg=cfg, backend=backend, env=ENV)
    return Built(
        cfg,
        Template.from_stack(data),
        Template.from_stack(backend),
        Template.from_stack(frontend),
        Template.from_stack(monitoring),
        backend,
        Template.from_stack(auth_stack) if auth_stack is not None else None,
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


@pytest.fixture(scope="session")
def connect(fake_backend: Path) -> Built:
    """Signed-in users may connect their own AWS account (-c awsConnect=on), first rollout: this account only."""
    return build("dev", fake_backend, aws_connect=True)


@pytest.fixture(scope="session")
def connect_two(fake_backend: Path) -> Built:
    return build("dev", fake_backend, aws_connect=True, connect_accounts=("111122223333", "444455556666"))


@pytest.fixture(scope="session")
def connect_any(fake_backend: Path) -> Built:
    """Any user's own account may connect (-c connectAccounts=any)."""
    return build("dev", fake_backend, aws_connect=True, connect_accounts=("*",))


@pytest.fixture(scope="session")
def open_live(fake_backend: Path) -> Built:
    """The live AI with no access code (-c requireAccessCode=off): signed-in users only, limited by the daily caps."""
    return build("dev", fake_backend, agent_provider="openai", require_access_code=False)


@pytest.fixture(scope="session")
def open_demo(fake_backend: Path) -> Built:
    """The deployment with sign-in switched off (-c auth=none): the open demo as it was before login."""
    return build("dev", fake_backend, auth="none")
