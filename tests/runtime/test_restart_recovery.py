"""Phase 6B-1：真实重启恢复验收。

关键要求：A 用完**必须 close**，B 使用**全新的 Engine / SessionFactory /
Repository / Service**，唯一共享状态是磁盘上的 SQLite 文件。
"""

from __future__ import annotations

from pathlib import Path

from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.review import HumanReviewAction
from security_diagnosis_harness.runtime import build_runtime_container


def _settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'restart.db').as_posix()}",
        auto_migrate=True,
    )


def _run_and_confirm(runtime) -> dict:
    service = runtime.service
    case = service.create_diagnosis(
        device_id="cam-offline-01",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="tester",
        description="摄像头黑屏",
    )
    run = service.run_diagnosis(case.diagnosis_id)
    service.review_diagnosis(
        diagnosis_id=case.diagnosis_id,
        action=HumanReviewAction.CONFIRM,
        reviewer="expert",
        comment="与现场一致",
    )
    final = service.get_diagnosis(case.diagnosis_id)
    return {
        "diagnosis_id": case.diagnosis_id,
        "status": final.status,
        "evidence_ids": [item.evidence_id for item in final.evidence],
        "evidence_hashes": [item.content_hash for item in final.evidence],
        "conclusion_id": final.conclusion.conclusion_id,
        "conclusion_summary": final.conclusion.summary,
        "cited_evidence_ids": list(final.conclusion.cited_evidence_ids),
        "review_id": final.reviews[0].review_id,
        "created_at": final.created_at,
        "updated_at": final.updated_at,
        "candidate_label": run.candidate_label,
    }


def test_restart_recovers_full_aggregate(tmp_path: Path):
    settings = _settings(tmp_path)

    runtime_a = build_runtime_container(settings)
    try:
        snapshot = _run_and_confirm(runtime_a)
    finally:
        runtime_a.close()

    runtime_b = build_runtime_container(settings)
    try:
        assert runtime_b is not runtime_a
        assert runtime_b.engine is not runtime_a.engine
        assert runtime_b.session_factory is not runtime_a.session_factory
        assert runtime_b.repository is not runtime_a.repository
        assert runtime_b.service is not runtime_a.service

        recovered = runtime_b.service.get_diagnosis(snapshot["diagnosis_id"])

        assert recovered.diagnosis_id == snapshot["diagnosis_id"]
        assert recovered.status is SecurityDiagnosisStatus.CONFIRMED
        assert [item.evidence_id for item in recovered.evidence] == snapshot["evidence_ids"]
        assert [item.content_hash for item in recovered.evidence] == snapshot["evidence_hashes"]
        assert recovered.conclusion is not None
        assert recovered.conclusion.conclusion_id == snapshot["conclusion_id"]
        assert recovered.conclusion.summary == snapshot["conclusion_summary"]
        assert list(recovered.conclusion.cited_evidence_ids) == snapshot["cited_evidence_ids"]
        assert recovered.reviews[0].review_id == snapshot["review_id"]
        assert recovered.created_at == snapshot["created_at"]
        assert recovered.updated_at == snapshot["updated_at"]
        assert recovered.created_at.tzinfo is not None
        assert recovered.updated_at.tzinfo is not None
    finally:
        runtime_b.close()


def test_restart_only_shares_sqlite_file(tmp_path: Path):
    settings = _settings(tmp_path)
    database_path = Path(settings.database_url.replace("sqlite:///", ""))

    runtime_a = build_runtime_container(settings)
    try:
        _run_and_confirm(runtime_a)
        assert database_path.exists()
    finally:
        runtime_a.close()

    # A 的 Engine 已释放，但文件仍在（这是唯一的共享状态）。
    assert database_path.exists()

    runtime_b = build_runtime_container(settings)
    try:
        assert runtime_b.engine is not runtime_a.engine
        assert runtime_b.repository is not runtime_a.repository
    finally:
        runtime_b.close()


def test_restart_recovers_multiple_diagnoses(tmp_path: Path):
    settings = _settings(tmp_path)

    runtime_a = build_runtime_container(settings)
    try:
        ids = []
        for device_id in ("cam-offline-01", "cam-channel-offline-01"):
            case = runtime_a.service.create_diagnosis(
                device_id=device_id,
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="tester",
            )
            runtime_a.service.run_diagnosis(case.diagnosis_id)
            ids.append(case.diagnosis_id)
    finally:
        runtime_a.close()

    runtime_b = build_runtime_container(settings)
    try:
        listed = {item.diagnosis_id for item in runtime_b.service.list_diagnoses()}
        assert set(ids) <= listed
    finally:
        runtime_b.close()


def test_memory_mode_does_not_survive_restart():
    """对照实验：memory 模式重启后不应有数据。"""
    settings = RuntimeSettings(repository_mode="memory")

    runtime_a = build_runtime_container(settings)
    try:
        case = runtime_a.service.create_diagnosis(
            device_id="cam-offline-01",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="tester",
        )
        diagnosis_id = case.diagnosis_id
    finally:
        runtime_a.close()

    runtime_b = build_runtime_container(settings)
    try:
        assert runtime_b.repository.exists(diagnosis_id) is False
    finally:
        runtime_b.close()
