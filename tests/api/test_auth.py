"""正式 API 身份认证与 review 授权边界。"""

from fastapi.testclient import TestClient

from security_diagnosis_harness.api.app import create_app
from security_diagnosis_harness.api.auth import BearerAuthenticator
from security_diagnosis_harness.bootstrap.container import build_service


def _client(authenticator: BearerAuthenticator) -> TestClient:
    return TestClient(create_app(build_service(), authenticator=authenticator))


def _create(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/diagnoses",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": "camera-3f-001",
            "fault_type": "camera_black_screen",
            "reporter": "spoofed-reporter",
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["reporter"] == "trusted-actor"
    return response.json()["data"]["diagnosis_id"]


def test_protected_api_rejects_missing_and_invalid_token():
    authenticator = BearerAuthenticator(
        "valid-token", actor="trusted-actor", roles=frozenset({"operator"})
    )
    with _client(authenticator) as client:
        assert client.get("/api/v1/diagnoses").status_code == 401
        response = client.get(
            "/api/v1/diagnoses", headers={"Authorization": "Bearer invalid"}
        )
        assert response.status_code == 401


def test_review_requires_reviewer_role():
    token = "operator-only-token"
    authenticator = BearerAuthenticator(
        token, actor="trusted-actor", roles=frozenset({"operator"})
    )
    with _client(authenticator) as client:
        diagnosis_id = _create(client, token)
        response = client.post(
            f"/api/v1/diagnoses/{diagnosis_id}/review",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "confirm", "reviewer": "spoofed-reviewer"},
        )
        assert response.status_code == 403


def test_review_actor_is_injected_from_authenticated_principal():
    token = "review-token"
    authenticator = BearerAuthenticator(
        token,
        actor="trusted-actor",
        roles=frozenset({"operator", "reviewer"}),
    )
    with _client(authenticator) as client:
        diagnosis_id = _create(client, token)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.post(
            f"/api/v1/diagnoses/{diagnosis_id}/runs", headers=headers
        ).status_code == 200
        response = client.post(
            f"/api/v1/diagnoses/{diagnosis_id}/review",
            headers=headers,
            json={"action": "confirm", "reviewer": "spoofed-reviewer"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["reviewer"] == "trusted-actor"


def test_authenticator_repr_never_contains_token():
    authenticator = BearerAuthenticator(
        "plain-secret-token", actor="trusted-actor", roles=frozenset({"operator"})
    )
    assert "plain-secret-token" not in repr(authenticator)
    assert "redacted" in repr(authenticator).lower()
