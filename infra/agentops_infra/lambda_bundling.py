"""Builds the Lambda deployment package without Docker.

CDK's default Python bundling needs a container. Here pip runs on the build machine but is told to
fetch Linux/arm64 wheels, so a Windows laptop and a GitHub runner produce the same package. If that
ever fails, CDK falls back to the Docker command below.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsii
from aws_cdk import AssetHashType, BundlingOptions, DockerImage, ILocalBundling
from aws_cdk import aws_lambda as lambda_

PYTHON_VERSION = "3.13"  # keep equal to RUNTIME and to the CI interpreter
RUNTIME = lambda_.Runtime.PYTHON_3_13
ARCHITECTURE = lambda_.Architecture.ARM_64
_WHEEL_PLATFORM = "manylinux2014_aarch64"
_PIP_ARGS = [
    "--no-cache-dir",
    "--no-compile",  # host-version .pyc files are useless on the Lambda interpreter
    "--only-binary=:all:",
    "--implementation", "cp",
    "--python-version", PYTHON_VERSION,
    "--platform", _WHEEL_PLATFORM,
]  # fmt: skip
_IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc")


@jsii.implements(ILocalBundling)
class _LocalBundling:
    def __init__(self, backend_dir: Path) -> None:
        self._backend = backend_dir

    def try_bundle(self, output_dir: str, *_args: Any, **_kwargs: Any) -> bool:
        out = Path(output_dir)
        subprocess.run(  # noqa: S603  (fixed argv, no shell, paths from this repo)
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--target",
                str(out),
                "-r",
                str(self._backend / "requirements-lambda.txt"),
                *_PIP_ARGS,
            ],  # fmt: skip
            check=True,
        )
        shutil.copytree(self._backend / "app", out / "app", ignore=_IGNORED)
        return True


def backend_code(backend_dir: Path) -> lambda_.Code:
    """The FastAPI app plus its pinned runtime dependencies, as a Lambda asset."""
    docker_command = (
        f"pip install -r requirements-lambda.txt -t /asset-output {' '.join(_PIP_ARGS)} && cp -r app /asset-output/app"
    )
    return lambda_.Code.from_asset(
        str(backend_dir),
        # Only inputs that change the package are hashed, so tests/venvs never trigger a redeploy.
        exclude=[".*", "tests", "**/__pycache__", "*.md", "pyproject.toml", "requirements.txt", "requirements-dev.txt"],
        asset_hash_type=AssetHashType.SOURCE,
        bundling=BundlingOptions(
            image=DockerImage.from_registry(f"public.ecr.aws/sam/build-python{PYTHON_VERSION}:latest-arm64"),
            command=["bash", "-c", docker_command],
            local=_LocalBundling(backend_dir),
        ),
    )
