"""SecurityFaultType 覆盖验收。"""

from __future__ import annotations

from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)


def test_fault_type_contains_camera_black_screen():
    assert SecurityFaultType.CAMERA_BLACK_SCREEN.value == "camera_black_screen"
    assert "camera_black_screen" in {item.value for item in SecurityFaultType}


def test_fault_type_covers_phase0_scenarios():
    values = {item.value for item in SecurityFaultType}
    assert values == {
        "camera_black_screen",
        "recording_missing",
        "access_card_failed",
        "alarm_false_positive",
    }


def test_diagnosis_status_covers_spec_states():
    values = {item.value for item in SecurityDiagnosisStatus}
    assert values == {
        "created",
        "investigating",
        "waiting_for_input",
        "waiting_for_confirmation",
        "confirmed",
        "rejected",
        "inconclusive",
    }
