"""测试公共夹具。"""

from __future__ import annotations

import pytest

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
