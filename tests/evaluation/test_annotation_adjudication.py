from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.evaluation import (
    AdjudicationAction,
    AdjudicationDecision,
    AnnotationConfidence,
    AnnotationProtocolError,
    BlindAnnotation,
    DatasetAdmissionCandidate,
    DatasetRegistry,
    DatasetSplit,
    adjudicate_annotations,
    build_annotation_task,
    compare_blind_annotations,
)

DATASET_ROOT = (
    Path(__file__).resolve().parents[2] / "datasets" / "security-diagnosis" / "1.0.0"
)


@pytest.fixture(scope="module")
def task():
    case = DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.VALIDATION)[0]
    return build_annotation_task(case)


def _annotation(task, reviewer, **changes):
    values = {
        "task_id": task.task_id,
        "reviewer": reviewer,
        "candidate_label": "access_time_window_denied",
        "necessary_tools": ("access__query_policy", "access__search_events"),
        "necessary_evidence_types": ("access_policy", "access_event"),
        "rationale": "不在授权时间窗口",
        "confidence": AnnotationConfidence.HIGH,
    }
    values.update(changes)
    return BlindAnnotation(**values)


def _decision(task, first, second, **changes):
    values = {
        "task_id": task.task_id,
        "first_annotation_id": first.annotation_id,
        "second_annotation_id": second.annotation_id,
        "adjudicator": "review-lead",
        "action": AdjudicationAction.APPROVE,
        "final_candidate_label": "access_time_window_denied",
        "final_tools": ("access__query_policy", "access__search_events"),
        "final_evidence_types": ("access_policy", "access_event"),
        "rationale": "两份盲标与设备事实一致",
    }
    values.update(changes)
    return AdjudicationDecision(**values)


def test_agreement_still_requires_explicit_adjudication(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    comparison = compare_blind_annotations(task, first, second)
    assert comparison.requires_adjudication is False
    candidate = adjudicate_annotations(task, first, second, _decision(task, first, second))
    assert isinstance(candidate, DatasetAdmissionCandidate)
    assert candidate.status == "candidate"


def test_disagreement_can_be_explicitly_resolved(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b", candidate_label="permission_not_granted")
    candidate = adjudicate_annotations(task, first, second, _decision(task, first, second))
    assert candidate.proposed_candidate_label == "access_time_window_denied"
    assert candidate.annotation_ids == (first.annotation_id, second.annotation_id)


@pytest.mark.parametrize(
    "action", [AdjudicationAction.REJECT, AdjudicationAction.NEEDS_REVISION]
)
def test_non_approve_decision_cannot_generate_candidate(task, action):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    decision = AdjudicationDecision(
        task_id=task.task_id,
        first_annotation_id=first.annotation_id,
        second_annotation_id=second.annotation_id,
        adjudicator="review-lead",
        action=action,
        rationale="需要重新核查",
    )
    with pytest.raises(AnnotationProtocolError, match="显式 approve"):
        adjudicate_annotations(task, first, second, decision)


def test_adjudicator_must_be_independent(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    decision = _decision(task, first, second, adjudicator="reviewer-a")
    with pytest.raises(AnnotationProtocolError, match="独立"):
        adjudicate_annotations(task, first, second, decision)


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": "another-task"},
        {"first_annotation_id": "another-annotation"},
        {"final_candidate_label": "invented-label"},
        {"final_tools": ("device__write",)},
        {"final_evidence_types": ("human_feedback",)},
        {"final_tools": ("access__query_policy", "access__query_policy")},
        {"final_evidence_types": ("access_policy", "access_policy")},
    ],
)
def test_mismatched_or_out_of_catalog_adjudication_is_rejected(task, changes):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    with pytest.raises(AnnotationProtocolError):
        adjudicate_annotations(task, first, second, _decision(task, first, second, **changes))


def test_approve_requires_complete_final_standard(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    with pytest.raises(ValidationError, match="完整标签"):
        _decision(task, first, second, final_tools=())


def test_non_approve_cannot_smuggle_final_answer(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    with pytest.raises(ValidationError, match="不得携带"):
        _decision(task, first, second, action=AdjudicationAction.REJECT)


def test_model_copy_cannot_bypass_adjudication_shape(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    invalid = _decision(task, first, second).model_copy(update={"final_tools": ()})
    with pytest.raises(AnnotationProtocolError, match="协议复验"):
        adjudicate_annotations(task, first, second, invalid)


def test_model_copy_cannot_expand_task_taxonomy(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    unsafe_task = task.model_copy(
        update={"candidate_label_options": (*task.candidate_label_options, "invented")}
    )
    with pytest.raises(AnnotationProtocolError, match="协议复验"):
        adjudicate_annotations(
            unsafe_task, first, second, _decision(task, first, second)
        )


def test_same_annotation_cannot_be_counted_twice(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b").model_copy(
        update={"annotation_id": first.annotation_id}
    )
    with pytest.raises(AnnotationProtocolError, match="annotation_id"):
        compare_blind_annotations(task, first, second)


def test_adjudication_text_is_redacted(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    decision = _decision(
        task,
        first,
        second,
        adjudicator="token=lead-secret",
        rationale="password=decision-secret",
    )
    candidate = adjudicate_annotations(task, first, second, decision)
    dumped = candidate.model_dump_json()
    assert "lead-secret" not in dumped
    assert "decision-secret" not in dumped
    assert "***REDACTED***" in dumped


def test_admission_candidate_is_not_a_dataset_case_or_writer(task):
    first = _annotation(task, "reviewer-a")
    second = _annotation(task, "reviewer-b")
    candidate = adjudicate_annotations(task, first, second, _decision(task, first, second))
    dumped = candidate.model_dump()
    assert "input_facts" not in dumped
    assert "dataset_version" not in dumped
    assert "split" not in dumped
    assert not hasattr(candidate, "save")
    assert not hasattr(candidate, "write")
