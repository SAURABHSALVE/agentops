#!/usr/bin/env python3
"""CDK entry point.

cdk deploy AgentOps-GitHubOidc                                one-off, admin identity, no stage needed
(repository and its GitHub subject prefix come from cdk.json: githubRepo, githubSubjectPrefix)
cdk deploy 'AgentOps-dev-*'  -c stage=dev  -c alertEmail=you@example.com
cdk deploy 'AgentOps-prod-*' -c stage=prod -c alertEmail=...   (CI does this on pushes to main)
"""

import os
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from agentops_infra import nag
from agentops_infra.backend_stack import BackendStack
from agentops_infra.config import REGION, github_subject_prefix, stage_config
from agentops_infra.data_stack import DataStack
from agentops_infra.frontend_stack import FrontendStack
from agentops_infra.github_oidc_stack import GitHubOidcStack
from agentops_infra.monitoring_stack import MonitoringStack

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

app = cdk.App()
# The account comes from whichever credentials run the CLI, so no account id is ever written down.
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=REGION)

oidc = GitHubOidcStack(
    app,
    "AgentOps-GitHubOidc",
    subject_prefix=github_subject_prefix(app),
    existing_provider_arn=app.node.try_get_context("existingOidcProviderArn"),
    env=env,
)
nag.acknowledge_oidc(oidc)
stacks: list[cdk.Stack] = [oidc]

if app.node.try_get_context("stage"):
    cfg = stage_config(app)
    name = f"AgentOps-{cfg.stage}"
    data = DataStack(app, f"{name}-Data", cfg=cfg, env=env)
    backend = BackendStack(app, f"{name}-Backend", cfg=cfg, data=data, backend_dir=ROOT / "backend", env=env)
    frontend = FrontendStack(
        app,
        f"{name}-Frontend",
        backend=backend,
        site_dir=ROOT / "frontend" / "out",
        placeholder_dir=HERE / "placeholder_site",
        env=env,
    )
    monitoring = MonitoringStack(app, f"{name}-Monitoring", cfg=cfg, backend=backend, env=env)
    nag.acknowledge_data(data, cfg)
    nag.acknowledge_backend(backend)
    nag.acknowledge_frontend(frontend)
    stacks += [data, backend, frontend, monitoring]
    for stack in (data, backend, frontend, monitoring):
        cdk.Tags.of(stack).add("Stage", cfg.stage)

for each in stacks:
    cdk.Tags.of(each).add("Project", "agentops")
cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))

app.synth()
