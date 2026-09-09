"""测试公共夹具。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.bootstrap.container import build_registry
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.device import (
    AlarmSeverity,
    Device,
    DeviceAlarmEvent,
    DeviceConfigSnapshot,
    DeviceSnapshot,
    DeviceType,
    RecordingStatus,
    StreamStatus,
)
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction
from security_diagnosis_harness.tools.contracts import ToolExecutionContext, ToolPermission
from security_diagnosis_harness.tools.recording_plan import RecordingPlanTool
from security_diagnosis_harness.tools.recording_playback import RecordingPlaybackTool
from security_diagnosis_harness.tools.registry import default_permissions
from security_diagnosis_harness.tools.storage_status import StorageStatusTool

# Phase 2B 录像缺失样例数据，路径在测试侧定义，避免改动 bootstrap 装配。
RECORDING_CASES_DATA_PATH = Path(__file__).resolve().parents[1] / (
    "samples/devices/recording_missing_cases.json"
)

DEVICE_ID = "camera-3f-001"


@pytest.fixture
def device() -> Device:
    return Device(
        device_id=DEVICE_ID,
        name="3 号楼大厅摄像头",
        device_type=DeviceType.CAMERA,
        vendor="sample-vendor",
        model="IPC-2000",
        location="3 号楼 1F 大厅",
        firmware_version="V5.7.0",
    )


@pytest.fixture
def snapshot() -> DeviceSnapshot:
    return DeviceSnapshot(
        device_id=DEVICE_ID,
        online=True,
        channel_online=True,
        stream_status=StreamStatus.ABNORMAL,
        recording_status=RecordingStatus.RECORDING,
    )


@pytest.fixture
def alarm_event() -> DeviceAlarmEvent:
    return DeviceAlarmEvent(
        device_id=DEVICE_ID,
        event_type="STREAM_PUBLISH_FAILED",
        severity=AlarmSeverity.CRITICAL,
        message="主码流发布失败",
    )


@pytest.fixture
def config_snapshot() -> DeviceConfigSnapshot:
    return DeviceConfigSnapshot(
        device_id=DEVICE_ID,
        enabled=True,
        encoding="H.265",
        resolution="2560x1440",
        frame_rate=25,
        bitrate_kbps=8192,
        config={"bitrate_mode": "CBR", "admin_password": "should-not-be-stored"},
    )


def make_evidence(
    diagnosis_id: str,
    summary: str = "设备在线但主码流异常",
    evidence_type: EvidenceType = EvidenceType.DEVICE_STATUS,
) -> DiagnosisEvidence:
    return DiagnosisEvidence(
        diagnosis_id=diagnosis_id,
        evidence_type=evidence_type,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary=summary,
        payload={"device_id": DEVICE_ID, "online": True, "stream_status": "abnormal"},
        reliability=Reliability.HIGH,
    )


def make_conclusion(diagnosis_id: str, evidence_ids: list[str]) -> DiagnosisConclusion:
    return DiagnosisConclusion(
        diagnosis_id=diagnosis_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        summary="摄像头黑屏更可能由码流发布失败或编码器异常导致",
        root_cause="STREAM_PUBLISH_FAILED",
        confidence=ConclusionConfidence.PROBABLE,
        cited_evidence_ids=evidence_ids,
        next_steps=["检查编码器状态", "降低码率后重试", "确认平台拉流状态"],
    )


def make_review(
    diagnosis_id: str,
    action: HumanReviewAction = HumanReviewAction.CONFIRM,
) -> HumanReview:
    return HumanReview(
        diagnosis_id=diagnosis_id,
        action=action,
        reviewer="ops-zhang",
        comment="现场确认为主码流异常",
    )


def make_case(diagnosis_id: str = "diag_phase0a") -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=diagnosis_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id=DEVICE_ID,
        reporter="ops-zhang",
        description="3 号楼大厅摄像头预览黑屏",
    )


def make_case_waiting_for_confirmation(diagnosis_id: str = "diag_phase0a") -> SecurityDiagnosisCase:
    """构造一条已有证据、已有候选结论、等待人工确认的诊断。"""
    case = make_case(diagnosis_id)
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    evidence = case.add_evidence(make_evidence(diagnosis_id))
    case.set_conclusion(make_conclusion(diagnosis_id, [evidence.evidence_id]))
    case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)
    return case


# ------------------------------------------------------------------ Phase 0B

DEVICE_DATASET: dict[str, object] = {
    "version": 1,
    "devices": [
        {
            "device_id": DEVICE_ID,
            "snapshot": {
                "online": True,
                "channel_online": True,
                "stream_status": "abnormal",
                "recording_status": "recording",
            },
            "alarms": [
                {
                    "event_type": "STREAM_PUBLISH_FAILED",
                    "severity": "critical",
                    "message": "主码流发布失败",
                },
                {
                    "event_type": "ENCODER_TIMEOUT",
                    "severity": "critical",
                    "message": "编码器响应超时",
                },
                {
                    "event_type": "DEVICE_ONLINE",
                    "severity": "info",
                    "message": "设备上线",
                },
            ],
            "config": {
                "enabled": True,
                "encoding": "H.265",
                "resolution": "2560x1440",
                "frame_rate": 25,
                "bitrate_kbps": 8192,
                "config": {
                    "bitrate_mode": "CBR",
                    "admin_password": "sample-placeholder-not-a-real-credential",
                },
            },
        }
    ],
}


def write_device_dataset(path: Path, dataset: dict[str, object] | None = None) -> Path:
    """把静态设备数据写入临时文件。"""
    path.write_text(json.dumps(dataset or DEVICE_DATASET, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def device_data_file(tmp_path: Path) -> Path:
    return write_device_dataset(tmp_path / "devices.json")


@pytest.fixture
def static_gateway(device_data_file: Path) -> StaticDeviceGateway:
    return StaticDeviceGateway(device_data_file)


def make_tool_context(
    diagnosis_id: str,
    *,
    gateway: StaticDeviceGateway | None = None,
    fault_type: SecurityFaultType = SecurityFaultType.CAMERA_BLACK_SCREEN,
    permissions: frozenset[ToolPermission] | None = None,
) -> ToolExecutionContext:
    """构造工具执行上下文，默认授予 Phase 0 只读权限。"""
    return ToolExecutionContext(
        diagnosis_id=diagnosis_id,
        fault_type=fault_type,
        permissions=permissions if permissions is not None else default_permissions(),
        device_gateway=gateway,
    )


@pytest.fixture
def tool_registry() -> object:
    """注册全部只读工具的 Registry（Phase 0 四个 + Phase 1 三个）。"""
    return build_registry()


@pytest.fixture
def recording_tool_registry(tool_registry) -> object:
    """在既有工具基础上追加 Phase 2B 三个录像只读工具。"""
    tool_registry.register(RecordingPlanTool())
    tool_registry.register(StorageStatusTool())
    tool_registry.register(RecordingPlaybackTool())
    return tool_registry


@pytest.fixture
def recording_gateway() -> StaticDeviceGateway:
    """读取 Phase 2B 录像缺失样例数据的静态网关。"""
    return StaticDeviceGateway(RECORDING_CASES_DATA_PATH)
