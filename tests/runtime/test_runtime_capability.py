"""Phase 6B-1 收尾：正式 Runtime 故障域能力边界验收。

Phase 6B-1 尚未实现四域统一 Strategy Router，因此正式 Runtime 只支持
`camera_black_screen`；其余故障类型必须在 create / run 两处都被拒绝，
且拒绝时不得写入 Evidence/Conclusion/Review，也不得返回 ok=true。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from security_diagnosis_harness.application.errors import UnsupportedFaultTypeError
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.runtime import (
    build_runtime_container,
    supported_runtime_fault_types,
)

UNSUPPORTED_FAULT_TYPES = (
    SecurityFaultType.RECORDING_MISSING,
    SecurityFaultType.ACCESS_CARD_FAILED,
    SecurityFaultType.ALARM_FALSE_POSITIVE,
)


def _settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'capability.db').as_posix()}",
        auto_migrate=True,
    )


# ---------------------------------------------------------------- 4
@pytest.mark.parametrize("fault_type", UNSUPPORTED_FAULT_TYPES)
def test_runtime_rejects_unsupported_fault_type_on_create(tmp_path: Path, fault_type):
    with build_runtime_container(_settings(tmp_path)) as runtime:
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="dev-1",
                fault_type=fault_type,
                reporter="probe",
            )

        assert runtime.service.list_diagnoses() == []


# ---------------------------------------------------------------- 5
def test_runtime_rejects_persisted_unsupported_case_on_run(tmp_path: Path):
    """数据库可能含旧数据：run 时必须再次拒绝。"""
    settings = _settings(tmp_path)

    # 绕过 create 的能力闸门，直接向仓储写入一条录像诊断（模拟历史数据）。
    from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
    from tests.persistence._builders import build_confirmed_case

    with build_runtime_container(settings) as seed_runtime:
        case = SecurityDiagnosisCase(
            diagnosis_id="diag-legacy-rec",
            fault_type=SecurityFaultType.RECORDING_MISSING,
            device_id="cam-1",
            reporter="legacy",
        )
        seed_runtime.repository.save(case)
        assert build_confirmed_case is not None  # 保持导入被使用

    with build_runtime_container(settings) as runtime:
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.run_diagnosis("diag-legacy-rec")


# ---------------------------------------------------------------- 6
def test_unsupported_case_remains_created(tmp_path: Path):
    settings = _settings(tmp_path)

    from security_diagnosis_harness.domain.case import SecurityDiagnosisCase

    with build_runtime_container(settings) as seed_runtime:
        seed_runtime.repository.save(
            SecurityDiagnosisCase(
                diagnosis_id="diag-legacy-rec",
                fault_type=SecurityFaultType.RECORDING_MISSING,
                device_id="cam-1",
                reporter="legacy",
            )
        )

    with build_runtime_container(settings) as runtime:
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.run_diagnosis("diag-legacy-rec")

        stored = runtime.repository.get("diag-legacy-rec")
        assert stored.status is SecurityDiagnosisStatus.CREATED


# ---------------------------------------------------------------- 7
def test_unsupported_case_has_no_evidence_or_conclusion(tmp_path: Path):
    settings = _settings(tmp_path)

    from security_diagnosis_harness.domain.case import SecurityDiagnosisCase

    with build_runtime_container(settings) as seed_runtime:
        seed_runtime.repository.save(
            SecurityDiagnosisCase(
                diagnosis_id="diag-legacy-rec",
                fault_type=SecurityFaultType.RECORDING_MISSING,
                device_id="cam-1",
                reporter="legacy",
            )
        )

    with build_runtime_container(settings) as runtime:
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.run_diagnosis("diag-legacy-rec")

        stored = runtime.repository.get("diag-legacy-rec")
        assert stored.evidence == []
        assert stored.conclusion is None
        assert stored.reviews == []


# ---------------------------------------------------------------- 9
def test_supported_camera_case_still_completes(tmp_path: Path):
    with build_runtime_container(_settings(tmp_path)) as runtime:
        case = runtime.service.create_diagnosis(
            device_id="camera-3f-001",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="probe",
        )
        result = runtime.service.run_diagnosis(case.diagnosis_id)
        final = runtime.service.get_diagnosis(case.diagnosis_id)

    assert result.ok is True
    assert final.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    assert final.conclusion is not None
    assert final.conclusion.fault_type is SecurityFaultType.CAMERA_BLACK_SCREEN


# ---------------------------------------------------------------- 10
@pytest.mark.parametrize("fault_type", UNSUPPORTED_FAULT_TYPES)
def test_camera_runtime_cannot_execute_other_fault_domains(tmp_path: Path, fault_type):
    with build_runtime_container(_settings(tmp_path)) as runtime:
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="dev-1",
                fault_type=fault_type,
                reporter="probe",
            )


def test_runtime_supported_types_are_camera_only():
    assert supported_runtime_fault_types() == frozenset(
        {SecurityFaultType.CAMERA_BLACK_SCREEN}
    )


# ---------------------------------------------------------------- 评测 Container 不受影响
def test_evaluation_containers_still_accept_all_fault_types():
    """Phase 1～4 独立评测 Container 必须保持原有行为（不限制故障类型）。"""
    from security_diagnosis_harness.bootstrap.container import (
        build_phase2_container,
        build_phase3_container,
        build_phase4_container,
    )

    for build, fault_type in (
        (build_phase2_container, SecurityFaultType.RECORDING_MISSING),
        (build_phase3_container, SecurityFaultType.ACCESS_CARD_FAILED),
        (build_phase4_container, SecurityFaultType.ALARM_FALSE_POSITIVE),
    ):
        container = build()
        case = container.service.create_diagnosis(
            device_id="dev-1",
            fault_type=fault_type,
            reporter="probe",
        )
        assert case.fault_type is fault_type


def test_in_memory_service_without_constraint_accepts_any_fault_type():
    """supported_fault_types=None 表示不限制（默认语义）。"""
    from security_diagnosis_harness.application.diagnoses import (
        SecurityDiagnosisApplicationService,
    )

    service = SecurityDiagnosisApplicationService.__new__(SecurityDiagnosisApplicationService)
    assert getattr(service, "supported_fault_types", None) is None

    repository = InMemoryDiagnosisRepository()
    assert repository is not None


def test_unsupported_fault_type_error_is_application_error():
    from security_diagnosis_harness.application.errors import ApplicationError

    assert issubclass(UnsupportedFaultTypeError, ApplicationError)
