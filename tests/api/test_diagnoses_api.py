"""诊断 API 验收：create -> run -> evidence -> review -> report。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from security_diagnosis_harness.api.app import create_app
from security_diagnosis_harness.bootstrap.container import build_service
from security_diagnosis_harness.domain.review import HumanReviewAction


@pytest.fixture
def client():
    app = create_app(build_service())
    with TestClient(app) as test_client:
        yield test_client


def create_diagnosis(client: TestClient) -> str:
    response = client.post(
        "/api/v1/diagnoses",
        json={
            "device_id": "camera-3f-001",
            "fault_type": "camera_black_screen",
            "reporter": "ops-zhang",
            "description": "3 号楼大厅摄像头预览黑屏",
        },
    )
    assert response.status_code == 201
    return response.json()["data"]["diagnosis_id"]


def test_create_diagnosis_returns_201(client):
    response = client.post(
        "/api/v1/diagnoses",
        json={
            "device_id": "camera-3f-001",
            "fault_type": "camera_black_screen",
            "reporter": "ops-zhang",
            "description": "摄像头黑屏",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "ok"
    assert body["data"]["status"] == "created"
    assert body["data"]["device_id"] == "camera-3f-001"
    assert body["data"]["evidence_count"] == 0


def test_create_diagnosis_rejects_invalid_payload(client):
    response = client.post(
        "/api/v1/diagnoses",
        json={"device_id": "", "fault_type": "camera_black_screen", "reporter": "ops"},
    )

    assert response.status_code == 422


def test_get_diagnosis_returns_case(client):
    diagnosis_id = create_diagnosis(client)

    response = client.get(f"/api/v1/diagnoses/{diagnosis_id}")

    assert response.status_code == 200
    assert response.json()["data"]["diagnosis_id"] == diagnosis_id


def test_run_diagnosis_returns_waiting_for_confirmation(client):
    diagnosis_id = create_diagnosis(client)

    response = client.post(f"/api/v1/diagnoses/{diagnosis_id}/runs")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["status"] == "waiting_for_confirmation"
    assert data["evidence_count"] >= 3
    assert data["conclusion"]["confidence"] == "probable"
    assert data["conclusion"]["cited_evidence_ids"]


def test_list_evidence_returns_evidence(client):
    diagnosis_id = create_diagnosis(client)
    client.post(f"/api/v1/diagnoses/{diagnosis_id}/runs")

    response = client.get(f"/api/v1/diagnoses/{diagnosis_id}/evidence")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) >= 3
    assert {item["evidence_type"] for item in data} >= {
        "device_status",
        "device_alarm",
        "device_config",
    }
    assert all(item["diagnosis_id"] == diagnosis_id for item in data)


def test_review_confirm_returns_confirmed(client):
    diagnosis_id = create_diagnosis(client)
    client.post(f"/api/v1/diagnoses/{diagnosis_id}/runs")

    response = client.post(
        f"/api/v1/diagnoses/{diagnosis_id}/review",
        json={"action": "confirm", "reviewer": "ops-li", "comment": "现场核实"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "confirmed"
    assert data["action"] == HumanReviewAction.CONFIRM.value
    assert client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]["status"] == "confirmed"


def test_review_reject_returns_rejected(client):
    diagnosis_id = create_diagnosis(client)
    client.post(f"/api/v1/diagnoses/{diagnosis_id}/runs")

    response = client.post(
        f"/api/v1/diagnoses/{diagnosis_id}/review",
        json={"action": "reject", "reviewer": "ops-li", "comment": "不一致"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"


def test_report_md_returns_markdown(client):
    diagnosis_id = create_diagnosis(client)
    client.post(f"/api/v1/diagnoses/{diagnosis_id}/runs")
    client.post(
        f"/api/v1/diagnoses/{diagnosis_id}/review",
        json={"action": "confirm", "reviewer": "ops-li"},
    )

    response = client.get(f"/api/v1/diagnoses/{diagnosis_id}/report.md")

    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    body = response.text
    assert body.startswith("# 安防设备诊断报告")
    assert diagnosis_id in body
    assert "confirmed" in body


def test_unknown_diagnosis_returns_controlled_404(client):
    for path in ("", "/runs", "/evidence", "/report.md"):
        method = client.post if path in ("/runs",) else client.get
        response = method(f"/api/v1/diagnoses/diag_not_exist{path}")
        assert response.status_code == 404, path
        assert response.json()["data"] is None
        assert response.json()["code"]


def test_review_unknown_diagnosis_returns_404(client):
    response = client.post(
        "/api/v1/diagnoses/diag_not_exist/review",
        json={"action": "confirm", "reviewer": "ops-li"},
    )

    assert response.status_code == 404


def test_review_before_run_returns_controlled_409(client):
    diagnosis_id = create_diagnosis(client)

    response = client.post(
        f"/api/v1/diagnoses/{diagnosis_id}/review",
        json={"action": "confirm", "reviewer": "ops-li"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "review_not_allowed"
    assert "Traceback" not in response.text


def test_health_still_works(client):
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ok"
    assert data["service"] == "security-diagnosis-harness"


def test_list_diagnoses_returns_created_cases(client):
    create_diagnosis(client)
    create_diagnosis(client)

    response = client.get("/api/v1/diagnoses")

    assert response.status_code == 200
    assert len(response.json()["data"]) == 2


def test_full_closed_loop(client):
    """create -> run -> evidence -> review -> report 一条链路跑通。"""
    diagnosis_id = create_diagnosis(client)

    run = client.post(f"/api/v1/diagnoses/{diagnosis_id}/runs").json()["data"]
    assert run["status"] == "waiting_for_confirmation"

    evidence = client.get(f"/api/v1/diagnoses/{diagnosis_id}/evidence").json()["data"]
    assert len(evidence) == run["evidence_count"]

    review = client.post(
        f"/api/v1/diagnoses/{diagnosis_id}/review",
        json={"action": "confirm", "reviewer": "ops-li", "comment": "同意"},
    ).json()["data"]
    assert review["status"] == "confirmed"

    report = client.get(f"/api/v1/diagnoses/{diagnosis_id}/report.md").text
    assert "已由人工确认" in report
    assert review["review_id"] not in report  # review_id 不出现在报告表格中
