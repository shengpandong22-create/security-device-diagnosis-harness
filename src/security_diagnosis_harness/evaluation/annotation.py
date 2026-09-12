"""Phase 8A-1 双人盲标任务、提交校验与一致性度量。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import new_id
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.domain.redaction import redact_mapping, redact_text
from security_diagnosis_harness.evaluation.dataset import DatasetCase
from security_diagnosis_harness.evaluation.taxonomy import evaluation_taxonomy


class AnnotationProtocolError(ValueError):
    """盲标任务或提交违反标注协议。"""


class AnnotationConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_CONFIDENCE_SCORE = {
    AnnotationConfidence.LOW: 0.25,
    AnnotationConfidence.MEDIUM: 0.5,
    AnnotationConfidence.HIGH: 1.0,
}


class AnnotationTask(BaseModel):
    """不含标准答案与模型输出的盲标任务。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(default_factory=lambda: new_id("ann_task"))
    case_id: str = Field(min_length=1)
    fault_type: SecurityFaultType
    input_facts: dict[str, object]
    allowed_tools: tuple[str, ...]
    candidate_label_options: tuple[str, ...]
    allowed_evidence_types: tuple[EvidenceType, ...]

    @model_validator(mode="after")
    def _validate_safe_task(self) -> AnnotationTask:
        safe_facts, changed = redact_mapping(self.input_facts)
        if changed or safe_facts != self.input_facts:
            raise ValueError("盲标任务事实必须预先脱敏")
        if not self.allowed_tools or not self.candidate_label_options:
            raise ValueError("盲标任务目录不能为空")
        if not self.allowed_evidence_types:
            raise ValueError("盲标任务 Evidence 目录不能为空")
        return self


class BlindAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    annotation_id: str = Field(default_factory=lambda: new_id("annotation"))
    task_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    candidate_label: str = Field(min_length=1)
    necessary_tools: tuple[str, ...]
    necessary_evidence_types: tuple[EvidenceType, ...]
    rationale: str = Field(min_length=1)
    confidence: AnnotationConfidence

    @model_validator(mode="after")
    def _redact_text(self) -> BlindAnnotation:
        reviewer, _ = redact_text(self.reviewer)
        rationale, _ = redact_text(self.rationale)
        object.__setattr__(self, "reviewer", reviewer)
        object.__setattr__(self, "rationale", rationale)
        return self


class AnnotationDisagreement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    first_annotation_id: str
    second_annotation_id: str
    first_label: str
    second_label: str
    label_agrees: bool
    tool_jaccard: float = Field(ge=0, le=1)
    evidence_jaccard: float = Field(ge=0, le=1)
    confidence_delta: float = Field(ge=0, le=1)
    requires_adjudication: bool


class AnnotationAgreementReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_count: int = Field(ge=1)
    label_agreement_rate: float = Field(ge=0, le=1)
    mean_tool_jaccard: float = Field(ge=0, le=1)
    mean_evidence_jaccard: float = Field(ge=0, le=1)
    mean_confidence_delta: float = Field(ge=0, le=1)
    label_kappa: float = Field(ge=-1, le=1)
    adjudication_rate: float = Field(ge=0, le=1)


def build_annotation_task(case: DatasetCase) -> AnnotationTask:
    """从案例构建盲标视图，刻意不复制 expected/source/split/budget 字段。"""
    taxonomy = evaluation_taxonomy(case.fault_type)
    return AnnotationTask(
        case_id=case.case_id,
        fault_type=case.fault_type,
        input_facts=case.input_facts,
        allowed_tools=case.allowed_tools,
        candidate_label_options=taxonomy.candidate_labels,
        allowed_evidence_types=taxonomy.evidence_types,
    )


def validate_blind_annotation(
    task: AnnotationTask, annotation: BlindAnnotation
) -> BlindAnnotation:
    if annotation.task_id != task.task_id:
        raise AnnotationProtocolError("标注意见不属于当前盲标任务")
    if annotation.candidate_label not in task.candidate_label_options:
        raise AnnotationProtocolError("候选标签不在当前故障域目录")
    if not annotation.necessary_tools or not set(annotation.necessary_tools).issubset(
        task.allowed_tools
    ):
        raise AnnotationProtocolError("必要工具必须是允许工具的非空子集")
    if not annotation.necessary_evidence_types or not set(
        annotation.necessary_evidence_types
    ).issubset(task.allowed_evidence_types):
        raise AnnotationProtocolError("必要 Evidence 必须是标准目录的非空子集")
    if len(set(annotation.necessary_tools)) != len(annotation.necessary_tools):
        raise AnnotationProtocolError("必要工具不允许重复")
    if len(set(annotation.necessary_evidence_types)) != len(
        annotation.necessary_evidence_types
    ):
        raise AnnotationProtocolError("必要 Evidence 不允许重复")
    return annotation


def compare_blind_annotations(
    task: AnnotationTask,
    first: BlindAnnotation,
    second: BlindAnnotation,
) -> AnnotationDisagreement:
    first = validate_blind_annotation(task, first)
    second = validate_blind_annotation(task, second)
    if first.reviewer == second.reviewer:
        raise AnnotationProtocolError("双人盲标必须由两名不同标注员完成")
    label_agrees = first.candidate_label == second.candidate_label
    tool_jaccard = _jaccard(first.necessary_tools, second.necessary_tools)
    evidence_jaccard = _jaccard(
        first.necessary_evidence_types, second.necessary_evidence_types
    )
    confidence_delta = abs(
        _CONFIDENCE_SCORE[first.confidence] - _CONFIDENCE_SCORE[second.confidence]
    )
    return AnnotationDisagreement(
        task_id=task.task_id,
        first_annotation_id=first.annotation_id,
        second_annotation_id=second.annotation_id,
        first_label=first.candidate_label,
        second_label=second.candidate_label,
        label_agrees=label_agrees,
        tool_jaccard=tool_jaccard,
        evidence_jaccard=evidence_jaccard,
        confidence_delta=confidence_delta,
        requires_adjudication=(
            not label_agrees or tool_jaccard < 1 or evidence_jaccard < 1
        ),
    )


def build_annotation_agreement_report(
    disagreements: Sequence[AnnotationDisagreement],
) -> AnnotationAgreementReport:
    if not disagreements:
        raise AnnotationProtocolError("一致性报告至少需要一组双人标注")
    return AnnotationAgreementReport(
        task_count=len(disagreements),
        label_agreement_rate=_mean(item.label_agrees for item in disagreements),
        mean_tool_jaccard=_mean(item.tool_jaccard for item in disagreements),
        mean_evidence_jaccard=_mean(item.evidence_jaccard for item in disagreements),
        mean_confidence_delta=_mean(item.confidence_delta for item in disagreements),
        label_kappa=_cohen_kappa(disagreements),
        adjudication_rate=_mean(item.requires_adjudication for item in disagreements),
    )


def _jaccard(first: Sequence[object], second: Sequence[object]) -> float:
    left, right = set(first), set(second)
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _mean(values: Iterable[float | bool]) -> float:
    items = tuple(float(item) for item in values)
    return sum(items) / len(items) if items else 0.0


def _cohen_kappa(disagreements: Sequence[AnnotationDisagreement]) -> float:
    observed = _mean(item.label_agrees for item in disagreements)
    first_counts: dict[str, int] = {}
    second_counts: dict[str, int] = {}
    for item in disagreements:
        first_counts[item.first_label] = first_counts.get(item.first_label, 0) + 1
        second_counts[item.second_label] = second_counts.get(item.second_label, 0) + 1
    total = len(disagreements)
    labels = set(first_counts) | set(second_counts)
    expected = sum(
        first_counts.get(label, 0) / total * second_counts.get(label, 0) / total
        for label in labels
    )
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)
