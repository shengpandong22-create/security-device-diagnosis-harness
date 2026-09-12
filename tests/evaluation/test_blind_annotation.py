from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.evaluation import (
    AnnotationConfidence,
    AnnotationProtocolError,
    BlindAnnotation,
    DatasetRegistry,
    DatasetSplit,
    build_annotation_agreement_report,
    build_annotation_task,
    compare_blind_annotations,
    validate_blind_annotation,
)

DATASET_ROOT = (
    Path(__file__).resolve().parents[2] / "datasets" / "security-diagnosis" / "1.0.0"
)


@pytest.fixture(scope="module")
def task():
    case = DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.VALIDATION)[0]
    return build_annotation_task(case)


def _annotation(task, reviewer="reviewer-a", **changes):
    values = {
        "task_id": task.task_id,
        "reviewer": reviewer,
        "candidate_label": "access_time_window_denied",
        "necessary_tools": ("access__query_policy", "access__search_events"),
        "necessary_evidence_types": ("access_policy", "access_event"),
        "rationale": "授权有效但不在允许时段",
        "confidence": AnnotationConfidence.HIGH,
    }
    values.update(changes)
    return BlindAnnotation(**values)


def test_annotation_task_contains_no_expected_or_model_output(task):
    dumped = task.model_dump()
    assert set(dumped) == {
        "task_id",
        "case_id",
        "fault_type",
        "input_facts",
        "allowed_tools",
        "candidate_label_options",
        "allowed_evidence_types",
    }
    rendered = task.model_dump_json()
    for forbidden in (
        "expected_candidate",
        "expected_tools",
        "required_evidence_types",
        "model_output",
        "source_record_id",
        '"split"',
    ):
        assert forbidden not in rendered


def test_annotation_task_exposes_complete_options(task):
    assert "access_time_window_denied" in task.candidate_label_options
    assert len(task.candidate_label_options) > 1
    assert "access_policy" in task.allowed_evidence_types


def test_valid_annotation_is_accepted(task):
    annotation = _annotation(task)
    validated = validate_blind_annotation(task, annotation)
    assert validated == annotation
    assert validated is not annotation


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": "other-task"},
        {"candidate_label": "model_invented_label"},
        {"necessary_tools": ("dangerous__write",)},
        {"necessary_tools": ()},
        {"necessary_evidence_types": ("human_feedback",)},
        {"necessary_evidence_types": ()},
        {"necessary_tools": ("access__query_policy", "access__query_policy")},
        {"necessary_evidence_types": ("access_policy", "access_policy")},
    ],
)
def test_invalid_annotation_is_rejected_by_task_contract(task, changes):
    with pytest.raises(AnnotationProtocolError):
        validate_blind_annotation(task, _annotation(task, **changes))


def test_annotation_free_text_is_redacted(task):
    annotation = _annotation(
        task,
        reviewer="token=reviewer-secret",
        rationale="password=annotation-secret",
    )
    dumped = annotation.model_dump_json()
    assert "reviewer-secret" not in dumped
    assert "annotation-secret" not in dumped
    assert "***REDACTED***" in dumped


def test_same_reviewer_cannot_impersonate_two_reviewers(task):
    with pytest.raises(AnnotationProtocolError, match="不同标注员"):
        compare_blind_annotations(task, _annotation(task), _annotation(task))


def test_identical_annotations_have_full_agreement(task):
    result = compare_blind_annotations(
        task,
        _annotation(task, "reviewer-a"),
        _annotation(task, "reviewer-b"),
    )
    assert result.label_agrees is True
    assert result.tool_jaccard == 1
    assert result.evidence_jaccard == 1
    assert result.confidence_delta == 0
    assert result.requires_adjudication is False


def test_tool_or_evidence_difference_requires_adjudication(task):
    result = compare_blind_annotations(
        task,
        _annotation(task, "reviewer-a"),
        _annotation(
            task,
            "reviewer-b",
            necessary_tools=("access__query_policy",),
            necessary_evidence_types=("access_policy",),
        ),
    )
    assert result.label_agrees is True
    assert result.tool_jaccard == 0.5
    assert result.evidence_jaccard == 0.5
    assert result.requires_adjudication is True


def test_label_difference_and_confidence_delta_are_recorded(task):
    result = compare_blind_annotations(
        task,
        _annotation(task, "reviewer-a"),
        _annotation(
            task,
            "reviewer-b",
            candidate_label="permission_not_granted",
            confidence=AnnotationConfidence.LOW,
        ),
    )
    assert result.label_agrees is False
    assert result.confidence_delta == 0.75
    assert result.requires_adjudication is True


def test_agreement_report_calculates_rates_and_kappa(task):
    agreement = compare_blind_annotations(
        task, _annotation(task, "a1"), _annotation(task, "b1")
    )
    disagreement = compare_blind_annotations(
        task,
        _annotation(task, "a2"),
        _annotation(task, "b2", candidate_label="permission_not_granted"),
    )
    report = build_annotation_agreement_report((agreement, disagreement))
    assert report.task_count == 2
    assert report.label_agreement_rate == 0.5
    assert report.mean_tool_jaccard == 1
    assert report.mean_evidence_jaccard == 1
    assert report.adjudication_rate == 0.5
    assert -1 <= report.label_kappa <= 1


def test_empty_agreement_report_is_rejected():
    with pytest.raises(AnnotationProtocolError, match="至少需要"):
        build_annotation_agreement_report(())


def test_unknown_annotation_fields_are_rejected(task):
    values = _annotation(task).model_dump()
    values["expected_candidate"] = "leaked-answer"
    with pytest.raises(ValidationError):
        BlindAnnotation.model_validate(values)
