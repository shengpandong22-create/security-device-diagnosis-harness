"""正式 API 身份认证与 review 授权边界。"""

import pytest
from fastapi.testclient import TestClient

from security_diagnosis_harness.api.app import create_app
from security_diagnosis_harness.api.auth import BearerAuthenticator
from security_diagnosis_harness.bootstrap.container import build_service


def _client(authenticator: BearerAuthenticator) -> TestClient:
    return TestClient(create_app(build_service(), authenticator=authenticator))


def _create(client: TestClient, token: str, expected_actor: str = "trusted-actor") -> str:
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
    assert response.json()["data"]["reporter"] == expected_actor
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
    operator_token = "operator-token"
    reviewer_token = "review-token"
    authenticator = BearerAuthenticator.for_separated_duties(
        operator_token=operator_token,
        operator_actor="trusted-operator",
        reviewer_token=reviewer_token,
        reviewer_actor="trusted-reviewer",
    )
    with _client(authenticator) as client:
        diagnosis_id = _create(client, operator_token, "trusted-operator")
        headers = {"Authorization": f"Bearer {operator_token}"}
        assert client.post(
            f"/api/v1/diagnoses/{diagnosis_id}/runs", headers=headers
        ).status_code == 200
        response = client.post(
            f"/api/v1/diagnoses/{diagnosis_id}/review",
            headers={"Authorization": f"Bearer {reviewer_token}"},
            json={"action": "confirm", "reviewer": "spoofed-reviewer"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["reviewer"] == "trusted-reviewer"


def test_separated_duties_rejects_reused_token_or_actor():
    with pytest.raises(ValueError, match="token"):
        BearerAuthenticator.for_separated_duties(
            operator_token="same",
            operator_actor="operator-a",
            reviewer_token="same",
            reviewer_actor="reviewer-b",
        )
    with pytest.raises(ValueError, match="actor"):
        BearerAuthenticator.for_separated_duties(
            operator_token="operator-token",
            operator_actor="same-actor",
            reviewer_token="reviewer-token",
            reviewer_actor="same-actor",
        )


def test_authenticator_repr_never_contains_token():
    authenticator = BearerAuthenticator(
        "plain-secret-token", actor="trusted-actor", roles=frozenset({"operator"})
    )
    assert "plain-secret-token" not in repr(authenticator)
    assert "redacted" in repr(authenticator).lower()
