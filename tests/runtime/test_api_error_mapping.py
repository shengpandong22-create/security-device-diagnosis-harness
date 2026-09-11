"""Phase 6B-1：API 异常映射与 health 验收。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from security_diagnosis_harness.api.app import create_app
from security_diagnosis_harness.application.errors import (
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
    KnowledgeAlreadyExistsError,
    KnowledgeNotFoundError,
    RepositoryPersistenceError,
)
from security_diagnosis_harness.bootstrap.container import build_container
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.runtime import build_runtime_container

INFRASTRUCTURE_LEAK_MARKERS = (
    "OperationalError",
    "IntegrityError",
    "sqlalchemy",
    "SELECT",
    "INSERT",
    "database_url",
    "sqlite:///",
    "Traceback",
    ".py",
    "C:\\",
    "/home/",
)


def _client(service) -> TestClient:
    return TestClient(create_app(service), raise_server_exceptions=False)


def _expect(service, error: Exception, status_code: int, code: str) -> None:
    def _raise(*_args, **_kwargs):
        raise error

    original = service.get_diagnosis
    service.get_diagnosis = _raise  # type: ignore[method-assign]
    try:
        response = _client(service).get("/api/v1/diagnoses/diag-x")
    finally:
        service.get_diagnosis = original  # type: ignore[method-assign]

    assert response.status_code == status_code
    body = response.json()
    assert body["code"] == code
    assert body["data"] is None


def test_not_found_maps_to_404():
    service = build_container().service
    _expect(service, DiagnosisNotFoundError("diag-x"), 404, "diagnosis_not_found")


def test_already_exists_maps_to_409():
    service = build_container().service
    _expect(service, DiagnosisAlreadyExistsError("diag-x"), 409, "diagnosis_already_exists")


def test_knowledge_not_found_maps_to_404():
    service = build_container().service
    _expect(service, KnowledgeNotFoundError("knw-x"), 404, "knowledge_not_found")


def test_knowledge_already_exists_maps_to_409():
    service = build_container().service
    _expect(service, KnowledgeAlreadyExistsError("knw-x"), 409, "knowledge_already_exists")


def test_concurrent_update_maps_to_409():
    """乐观锁冲突必须 409，且不暴露 expected/actual version。"""
    from security_diagnosis_harness.application.errors import ConcurrentUpdateError

    service = build_container().service
    service.get_diagnosis = _raiser(  # type: ignore[method-assign]
        ConcurrentUpdateError("诊断", "diag-x", 3)
    )

    response = _client(service).get("/api/v1/diagnoses/diag-x")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "concurrent_update"
    assert body["message"] == "诊断已被其他请求更新，请刷新后重试"
    assert body["data"] is None
    # 不得泄漏版本号与内部细节
    assert "3" not in response.text
    assert "version" not in response.text.lower()


def test_unsupported_fault_type_maps_to_422():
    """正式 Runtime 不支持录像 / 门禁 / 报警时必须 422 且不返回成功。"""
    import tempfile
    from pathlib import Path as _Path

    from security_diagnosis_harness.config import RuntimeSettings
    from security_diagnosis_harness.runtime import build_runtime_container

    tmpdir = _Path(tempfile.mkdtemp(prefix="phase6b-422-"))
    settings = RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmpdir / 'cap.db').as_posix()}",
    )
    with build_runtime_container(settings) as runtime:
        client = TestClient(create_app(runtime.service))
        response = client.post(
            "/api/v1/diagnoses",
            json={
                "device_id": "cam-rec-plan-disabled-01",
                "fault_type": "recording_missing",
                "reporter": "probe",
            },
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "unsupported_fault_type"
    assert body["data"] is None
    serialized = response.text
    for marker in ("ToolRegistry", "FakeLLM", ".py", "sqlite:///", "class "):
        assert marker not in serialized


def test_repository_persistence_error_maps_to_503():
    service = build_container().service
    _expect(
        service,
        RepositoryPersistenceError("诊断", "operational"),
        503,
        "repository_unavailable",
    )


def test_503_body_does_not_leak_infrastructure():
    service = build_container().service
    service.get_diagnosis = _raiser(  # type: ignore[method-assign]
        RepositoryPersistenceError("诊断", "OperationalError")
    )

    response = _client(service).get("/api/v1/diagnoses/diag-x")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "repository_unavailable"
    assert body["message"] == "诊断数据暂时不可用"
    serialized = response.text
    for marker in INFRASTRUCTURE_LEAK_MARKERS:
        assert marker not in serialized


def _raiser(error: Exception):
    def _raise(*_args, **_kwargs):
        raise error

    return _raise


# ---------------------------------------------------------------- health
def test_health_default_assembly_reports_memory_and_ready():
    client = TestClient(create_app(build_container().service))

    body = client.get("/health").json()

    assert body["code"] == "ok"
    data = body["data"]
    assert data["status"] == "ok"
    assert data["phase"] == "6B"
    assert data["repository_mode"] == "memory"
    assert data["database_ready"] is True


def _runtime_client(runtime) -> TestClient:
    """用运行时真实状态构建 app（health 反映 repository_mode 与就绪状态）。"""
    return TestClient(
        create_app(
            runtime.service,
            repository_mode=runtime.settings.repository_mode.value,
            database_ready=runtime.database_ready,
        )
    )


def test_health_reports_sqlite_runtime(tmp_path: Path):
    settings = RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'health.db').as_posix()}",
    )
    with build_runtime_container(settings) as runtime:
        body = _runtime_client(runtime).get("/health").json()

    data = body["data"]
    assert data["repository_mode"] == "sqlite"
    assert data["database_ready"] is True


def test_health_reports_not_ready_after_close(tmp_path: Path):
    settings = RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'closed.db').as_posix()}",
    )
    runtime = build_runtime_container(settings)
    client = _runtime_client(runtime)
    runtime.close()

    body = client.get("/health").json()

    assert body["data"]["database_ready"] is False


def test_health_does_not_leak_database_location(tmp_path: Path):
    settings = RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'secret-dir' / 'secret.db').as_posix()}",
    )
    with build_runtime_container(settings) as runtime:
        response = _runtime_client(runtime).get("/health")

    serialized = response.text
    assert "secret.db" not in serialized
    assert "secret-dir" not in serialized
    assert "sqlite:///" not in serialized
    assert str(tmp_path) not in serialized


def test_health_backward_compatible_fields_present():
    client = TestClient(create_app(build_container().service))

    data = client.get("/health").json()["data"]

    assert {"status", "service", "version", "phase"} <= set(data)


def test_existing_api_flow_still_works():
    service = build_container().service
    client = _client(service)

    created = client.post(
        "/api/v1/diagnoses",
        json={
            "device_id": "cam-offline-01",
            "fault_type": "camera_black_screen",
            "reporter": "tester",
        },
    )
    assert created.status_code == 201
    diagnosis_id = created.json()["data"]["diagnosis_id"]

    run = client.post(f"/api/v1/diagnoses/{diagnosis_id}/runs")
    assert run.status_code == 200
    assert run.json()["data"]["status"] == SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION.value
