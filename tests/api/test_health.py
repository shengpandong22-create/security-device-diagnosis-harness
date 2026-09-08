"""最小 API 验收：GET /health。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from security_diagnosis_harness import __version__
from security_diagnosis_harness.api.app import create_app


def test_health_returns_200():
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "ok"
    assert body["data"]["status"] == "ok"
    assert body["data"]["service"] == "security-diagnosis-harness"
    assert body["data"]["version"] == __version__
