"""最小 API 验收：GET /health。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from security_diagnosis_harness import __version__
from security_diagnosis_harness.api.app import create_app


def test_health_returns_200():
    with TestClient(create_app(demo_mode=True)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "ok"
    assert body["data"]["status"] == "ok"
    assert body["data"]["service"] == "security-diagnosis-harness"
    assert body["data"]["version"] == __version__


def test_health_needs_no_authentication_even_in_formal_mode():
    """health 是运维探针，正式模式下也不要求凭证。"""
    from security_diagnosis_harness.api.auth import BearerAuthenticator
    from security_diagnosis_harness.bootstrap.container import build_service

    authenticator = BearerAuthenticator(
        "operator-token", actor="ops", roles=frozenset({"operator"})
    )
    app = create_app(build_service(), authenticator=authenticator)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
