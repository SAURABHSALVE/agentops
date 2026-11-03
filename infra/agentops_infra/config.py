"""Deployment configuration, read from CDK context (`-c key=value`, or cdk.json defaults).

Nothing sensitive has a default here: the stage must be chosen explicitly and the alert email is
supplied at deploy time (a GitHub variable in CI), so it is never committed.
"""

import re
from dataclasses import dataclass

from constructs import Construct

STAGES = ("dev", "prod")
REGION = "us-east-1"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
# GitHub's subject prefix: "repo:OWNER/NAME", or the immutable "repo:OWNER@OWNER_ID/NAME@REPO_ID".
_SUBJECT_PREFIX = re.compile(r"^repo:([A-Za-z0-9-]+)(?:@\d+)?/([A-Za-z0-9._-]+)(?:@\d+)?$")
_MODEL = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
AGENT_PROVIDERS = ("mock", "openai")
AUTH_MODES = ("cognito", "none")
_ACCOUNT = re.compile(r"^\d{12}$")


class ConfigError(ValueError):
    """Bad or missing deployment configuration. Raised at synth time, before anything is deployed."""


@dataclass(frozen=True)
class StageConfig:
    stage: str
    alert_email: str
    budget_limit_usd: int
    # "mock" (default) deploys exactly what existed before. "openai" turns on the live model and, with it, the
    # access code and the two secrets the Lambda may read. Opt in with: -c agentProvider=openai
    agent_provider: str = "mock"
    openai_model: str = "gpt-4.1-mini"
    # "cognito" (default) adds the sign-in user pool: writes need a signed-in user and records are per user.
    # "none" deploys the open demo as it was before sign-in. Choose with: -c auth=none
    auth: str = "cognito"
    # Off by default. "on" lets signed-in users connect their own AWS account (read-only, through a role they create):
    # the Lambda gets a fixed named role and may call sts:AssumeRole on `AgentOpsReadOnly` in the accounts below, and
    # nothing else. Needs sign-in. Opt in with: -c awsConnect=on
    aws_connect: bool = False
    # Accounts whose role may be assumed. Empty means only the account AgentOps itself runs in (the first rollout).
    # Widen with: -c connectAccounts=ACCOUNT_ID,ACCOUNT_ID
    connect_accounts: tuple[str, ...] = ()

    @property
    def sign_in(self) -> bool:
        return self.auth == "cognito"

    @property
    def live(self) -> bool:
        return self.agent_provider == "openai"

    @property
    def is_prod(self) -> bool:
        return self.stage == "prod"

    @property
    def prefix(self) -> str:
        return f"agentops-{self.stage}"

    @property
    def log_retention_days(self) -> int:
        return 30 if self.is_prod else 7


def _context(scope: Construct, key: str) -> str | None:
    value = scope.node.try_get_context(key)
    return None if value is None else str(value).strip()


def stage_config(scope: Construct) -> StageConfig:
    stage = _context(scope, "stage")
    if stage not in STAGES:
        raise ConfigError(f"Set the stage explicitly: -c stage=dev|prod (got {stage!r}).")
    email = _context(scope, "alertEmail")
    if not email or not _EMAIL.match(email):
        raise ConfigError("Set a valid alert address: -c alertEmail=you@example.com (never commit it).")
    raw_limit = _context(scope, "budgetLimitUsd") or "10"
    if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= 100_000:
        raise ConfigError("budgetLimitUsd must be a whole number of dollars between 1 and 100000.")
    provider = _context(scope, "agentProvider") or "mock"
    if provider not in AGENT_PROVIDERS:
        raise ConfigError("agentProvider must be 'mock' or 'openai'.")
    model = _context(scope, "openaiModel") or "gpt-4.1-mini"
    if not _MODEL.match(model):
        raise ConfigError("openaiModel is not a valid model name.")
    auth = _context(scope, "auth") or "cognito"
    if auth not in AUTH_MODES:
        raise ConfigError("auth must be 'cognito' or 'none'.")
    connect = (_context(scope, "awsConnect") or "off").lower()
    if connect not in ("on", "off"):
        raise ConfigError("awsConnect must be 'on' or 'off'.")
    accounts = tuple(a.strip() for a in (_context(scope, "connectAccounts") or "").split(",") if a.strip())
    if any(not _ACCOUNT.match(a) for a in accounts):
        raise ConfigError("connectAccounts must be a comma-separated list of 12-digit AWS account ids.")
    if connect == "on" and auth != "cognito":
        raise ConfigError("awsConnect=on needs sign-in: a connection belongs to one signed-in user (use auth=cognito).")
    if accounts and connect != "on":
        raise ConfigError("connectAccounts only applies together with awsConnect=on.")
    return StageConfig(
        auth=auth,
        aws_connect=connect == "on",
        connect_accounts=accounts,
        stage=stage,
        alert_email=email,
        budget_limit_usd=int(raw_limit),
        agent_provider=provider,
        openai_model=model,
    )


def github_repo(scope: Construct) -> str:
    repo = _context(scope, "githubRepo")
    if not repo or not _REPO.match(repo):
        raise ConfigError("githubRepo must look like 'owner/name'.")
    return repo


def github_subject_prefix(scope: Construct) -> str:
    """The `sub` claim prefix GitHub puts in its tokens for this repository.

    Repositories with immutable subject claims (GitHub's default for newer ones) sign
    `repo:OWNER@OWNER_ID/NAME@REPO_ID:...`, not `repo:OWNER/NAME:...`; a trust policy written for the
    name-based form never matches. Read the real value with
    `gh api repos/OWNER/NAME/actions/oidc/customization/sub` (field `sub_claim_prefix`). It must name the same
    repository as `githubRepo`, so a typo cannot silently trust a different one.
    """
    repo = github_repo(scope)
    raw = _context(scope, "githubSubjectPrefix")
    if not raw:
        return f"repo:{repo}"
    match = _SUBJECT_PREFIX.match(raw)
    if not match or f"{match.group(1)}/{match.group(2)}" != repo:
        raise ConfigError("githubSubjectPrefix must be GitHub's 'repo:OWNER@ID/NAME@ID' for the same githubRepo.")
    return raw
