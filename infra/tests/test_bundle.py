"""The real Lambda package: built without Docker, for Linux/arm64, from the repo's backend/."""

from pathlib import Path

from agentops_infra.lambda_bundling import _LocalBundling

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_local_bundle_has_the_app_and_linux_arm64_wheels(tmp_path: Path) -> None:
    assert _LocalBundling(BACKEND).try_bundle(str(tmp_path))
    assert (tmp_path / "app" / "lambda_handler.py").is_file()
    assert (tmp_path / "mangum").is_dir() and (tmp_path / "fastapi").is_dir()
    assert list((tmp_path / "pydantic_core").glob("_pydantic_core.cpython-313-aarch64-linux-gnu.so"))
    assert not list(tmp_path.rglob("*.pyc")), "no compiled caches in the package"
    assert not (tmp_path / "boto3").exists(), "boto3 comes from the Lambda runtime"
    assert not (tmp_path / "uvicorn").exists(), "uvicorn is for local runs only"
