"""人工确认验收：confirmed 只能由 HumanReview confirm 产生。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.domain.errors import (
    EvidenceDiagnosisMismatch,
    ReviewNotAllowed,
)
from security_diagnosis_harness.domain.review import HumanReviewAction

from ..conftest import make_case, make_case_waiting_for_confirmation, make_review


def test_confirm_produces_confirmed_status():
    case = make_case_waiting_for_confirmation()
    review = case.apply_human_review(make_review(case.diagnosis_id))

    assert case.status is SecurityDiagnosisStatus.CONFIRMED
    assert case.reviews == [review]
    assert review.action is HumanReviewAction.CONFIRM


def test_confirm_requires_waiting_for_confirmation_state():
    case = make_case()
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)

    with pytest.raises(ReviewNotAllowed):
        case.apply_human_review(make_review(case.diagnosis_id))

    assert case.status is SecurityDiagnosisStatus.INVESTIGATING
    assert case.reviews == []


def test_confirm_requires_candidate_conclusion():
    case = make_case()
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)

    with pytest.raises(ReviewNotAllowed):
        case.apply_human_review(make_review(case.diagnosis_id))


def test_reject_moves_to_rejected():
    case = make_case_waiting_for_confirmation()
    case.apply_human_review(
        make_review(case.diagnosis_id, action=HumanReviewAction.REJECT)
    )

    assert case.status is SecurityDiagnosisStatus.REJECTED


def test_request_more_info_moves_to_waiting_for_input():
    case = make_case_waiting_for_confirmation()
    case.apply_human_review(
        make_review(case.diagnosis_id, action=HumanReviewAction.REQUEST_MORE_INFO)
    )

    assert case.status is SecurityDiagnosisStatus.WAITING_FOR_INPUT


def test_review_must_belong_to_the_diagnosis():
    case = make_case_waiting_for_confirmation()

    with pytest.raises(EvidenceDiagnosisMismatch):
        case.apply_human_review(make_review("diag_other"))


def test_confirmed_terminal_case_cannot_be_reviewed_again():
    case = make_case_waiting_for_confirmation()
    case.apply_human_review(make_review(case.diagnosis_id))

    with pytest.raises(ReviewNotAllowed):
        case.apply_human_review(
            make_review(case.diagnosis_id, action=HumanReviewAction.REJECT)
        )

    assert case.status is SecurityDiagnosisStatus.CONFIRMED
