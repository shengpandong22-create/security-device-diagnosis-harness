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


# ---------------------------------------------------------------- 7 正式入口边界
def _separated_duties_client() -> TestClient:
    authenticator = BearerAuthenticator.for_separated_duties(
        operator_token="operator-token",
        operator_actor="trusted-operator",
        reviewer_token="reviewer-token",
        reviewer_actor="trusted-reviewer",
    )
    return TestClient(create_app(build_service(), authenticator=authenticator))


_OPERATOR_HEADERS = {"Authorization": "Bearer operator-token"}
_REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}
_NEW_DIAGNOSIS = {
    "device_id": "camera-3f-001",
    "fault_type": "camera_black_screen",
    "reporter": "spoofed-reporter",
}


def test_operator_cannot_review_and_reviewer_cannot_operate():
    with _separated_duties_client() as client:
        created = client.post(
            "/api/v1/diagnoses", headers=_OPERATOR_HEADERS, json=_NEW_DIAGNOSIS
        )
        assert created.status_code == 201
        diagnosis_id = created.json()["data"]["diagnosis_id"]

        # reviewer 不能创建 / 运行
        assert (
            client.post(
                "/api/v1/diagnoses", headers=_REVIEWER_HEADERS, json=_NEW_DIAGNOSIS
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/diagnoses/{diagnosis_id}/runs", headers=_REVIEWER_HEADERS
            ).status_code
            == 403
        )
        assert (
            client.get("/api/v1/diagnoses", headers=_REVIEWER_HEADERS).status_code == 403
        )

        # operator 不能 review
        assert client.post(
            f"/api/v1/diagnoses/{diagnosis_id}/review",
            headers=_OPERATOR_HEADERS,
            json={"action": "confirm", "reviewer": "spoofed"},
        ).status_code == 403


def test_principal_with_both_roles_is_rejected_on_both_paths():
    """即使凭证同时带两种角色，也不能既操作又审核。"""
    authenticator = BearerAuthenticator(
        "dual-token", actor="dual-actor", roles=frozenset({"operator", "reviewer"})
    )
    headers = {"Authorization": "Bearer dual-token"}
    with TestClient(create_app(build_service(), authenticator=authenticator)) as client:
        assert client.get("/api/v1/diagnoses", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/v1/diagnoses/diag_any/review",
                headers=headers,
                json={"action": "confirm", "reviewer": "spoofed"},
            ).status_code
            == 403
        )


def test_actor_cannot_review_own_diagnosis():
    """同一 actor 不能自审：reviewer 与上报人相同即拒绝。"""
    from security_diagnosis_harness.domain.enums import SecurityFaultType

    service = build_service()
    case = service.create_diagnosis(
        device_id="camera-3f-001",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="alice",
    )
    service.run_diagnosis(case.diagnosis_id)
    authenticator = BearerAuthenticator(
        "reviewer-token", actor="alice", roles=frozenset({"reviewer"})
    )

    with TestClient(create_app(service, authenticator=authenticator)) as client:
        response = client.post(
            f"/api/v1/diagnoses/{case.diagnosis_id}/review",
            headers={"Authorization": "Bearer reviewer-token"},
            json={"action": "confirm", "reviewer": "spoofed"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "self_review_not_allowed"


def test_formal_mode_ignores_body_declared_actor_on_review():
    """正式模式下 review 的 actor 只能来自认证 principal。"""
    from security_diagnosis_harness.domain.enums import SecurityFaultType

    service = build_service()
    case = service.create_diagnosis(
        device_id="camera-3f-001",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="ops-zhang",
    )
    service.run_diagnosis(case.diagnosis_id)
    authenticator = BearerAuthenticator.for_separated_duties(
        operator_token="operator-token",
        operator_actor="trusted-operator",
        reviewer_token="reviewer-token",
        reviewer_actor="trusted-reviewer",
    )

    with TestClient(create_app(service, authenticator=authenticator)) as client:
        response = client.post(
            f"/api/v1/diagnoses/{case.diagnosis_id}/review",
            headers=_REVIEWER_HEADERS,
            json={"action": "confirm", "reviewer": "spoofed-reviewer"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["reviewer"] == "trusted-reviewer"


def test_unauthenticated_app_requires_explicit_demo_mode():
    from security_diagnosis_harness.config import RuntimeConfigurationError

    with pytest.raises(RuntimeConfigurationError, match="Demo"):
        create_app(build_service())

    # 显式 Demo 才允许无认证运行，且正文自报身份生效。
    app = create_app(build_service(), demo_mode=True)
    assert app.state.demo_mode is True
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        created = client.post(
            "/api/v1/diagnoses",
            json={
                "device_id": "camera-3f-001",
                "fault_type": "camera_black_screen",
                "reporter": "demo-reporter",
            },
        )
        assert created.json()["data"]["reporter"] == "demo-reporter"
