"""SecurityDiagnosisCase 创建与状态机验收。"""

from __future__ import annotations

import pydantic
import pytest

from security_diagnosis_harness.domain.case import (
    ALLOWED_STATUS_TRANSITIONS,
    TERMINAL_STATUSES,
    SecurityDiagnosisCase,
)
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.errors import InvalidStatusTransition, ReviewNotAllowed
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction

from ..conftest import DEVICE_ID, make_case


def test_create_case_with_defaults():
    case = make_case()

    assert case.diagnosis_id
    assert case.fault_type is SecurityFaultType.CAMERA_BLACK_SCREEN
    assert case.device_id == DEVICE_ID
    assert case.status is SecurityDiagnosisStatus.CREATED
    assert case.evidence == []
    assert case.conclusion is None
    assert case.reviews == []
    assert case.created_at <= case.updated_at


def test_case_rejects_blank_device_id():
    with pytest.raises(pydantic.ValidationError):
        SecurityDiagnosisCase(
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            device_id="",
            reporter="ops-zhang",
        )


def test_legal_transition_updates_status():
    case = make_case()
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)

    assert case.status is SecurityDiagnosisStatus.INVESTIGATING


def test_illegal_transition_is_rejected():
    case = make_case()

    with pytest.raises(InvalidStatusTransition):
        case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)


def test_terminal_status_cannot_transition():
    case = make_case()
    case.transition_to(SecurityDiagnosisStatus.INCONCLUSIVE)

    with pytest.raises(InvalidStatusTransition):
        case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)


def test_confirmed_is_unreachable_from_every_state():
    assert SecurityDiagnosisStatus.CONFIRMED in TERMINAL_STATUSES
    for status in SecurityDiagnosisStatus:
        assert SecurityDiagnosisStatus.CONFIRMED not in ALLOWED_STATUS_TRANSITIONS[status]


def test_transition_to_confirmed_always_raises():
    case = make_case()
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)

    with pytest.raises(InvalidStatusTransition):
        case.transition_to(SecurityDiagnosisStatus.CONFIRMED)

    assert case.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION


def test_direct_assignment_cannot_forge_confirmed_case():
    case = make_case()

    with pytest.raises(InvalidStatusTransition, match="状态机"):
        case.status = SecurityDiagnosisStatus.CONFIRMED

    assert case.status is SecurityDiagnosisStatus.CREATED


def test_constructor_rejects_confirmed_case_with_foreign_review():
    review = HumanReview(
        diagnosis_id="other-diagnosis",
        action=HumanReviewAction.CONFIRM,
        reviewer="expert",
    )

    with pytest.raises((pydantic.ValidationError, ReviewNotAllowed), match="diagnosis_id"):
        SecurityDiagnosisCase(
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            device_id=DEVICE_ID,
            reporter="ops-zhang",
            status=SecurityDiagnosisStatus.CONFIRMED,
            reviews=[review],
        )
