"""The Lambda entry point serves the real FastAPI app for API Gateway HTTP API (payload v2) events."""

import json
from typing import Any

import pytest

pytest.importorskip("mangum")


def _event(method: str, path: str) -> dict[str, Any]:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "example.execute-api.us-east-1.amazonaws.com"},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "example",
            "domainName": "example.execute-api.us-east-1.amazonaws.com",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.9",
                "userAgent": "pytest",
            },
            "requestId": "r1",
            "routeKey": "$default",
            "stage": "$default",
            "time": "30/Oct/2026:12:00:00 +0000",
            "timeEpoch": 1793361600000,
        },
        "isBase64Encoded": False,
    }


def _invoke(method: str, path: str) -> dict[str, Any]:
    from app.lambda_handler import handler

    no_context: Any = None  # the app never reads the Lambda context
    result: dict[str, Any] = handler(_event(method, path), no_context)
    return result


def test_health_through_the_handler() -> None:
    result = _invoke("GET", "/health")
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"status": "ok", "data_mode": "simulated", "version": "0.1.0"}


def test_a_post_completes_the_investigation_before_the_invocation_returns() -> None:
    started = _invoke("POST", "/api/incidents/INC-1001/investigations")
    assert started["statusCode"] == 202
    inv_id = json.loads(started["body"])["id"]
    done = json.loads(_invoke("GET", f"/api/investigations/{inv_id}")["body"])
    assert done["status"] == "completed"


def test_domain_errors_keep_their_envelope() -> None:
    result = _invoke("GET", "/api/incidents/INC-9999")
    assert result["statusCode"] == 404
    assert json.loads(result["body"])["error"]["code"] == "incident_not_found"
