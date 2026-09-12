"""Phase 8A-1 双人盲标任务、提交校验与一致性度量。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path

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


class AdjudicationAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_REVISION = "needs_revision"


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
        taxonomy = evaluation_taxonomy(self.fault_type)
        if self.candidate_label_options != taxonomy.candidate_labels:
            raise ValueError("盲标任务必须公开完整候选标签目录")
        if self.allowed_evidence_types != taxonomy.evidence_types:
            raise ValueError("盲标任务必须公开完整 Evidence 目录")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("盲标任务工具目录不允许重复")
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
    case_id: str
    fault_type: SecurityFaultType
    first_annotation_id: str
    second_annotation_id: str
    first_label: str
    second_label: str
    label_agrees: bool
    tool_jaccard: float = Field(ge=0, le=1)
    evidence_jaccard: float = Field(ge=0, le=1)
    confidence_delta: float = Field(ge=0, le=1)
    requires_adjudication: bool


class FaultTypeAgreementSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fault_type: SecurityFaultType
    task_count: int = Field(ge=1)
    label_agreement_rate: float = Field(ge=0, le=1)
    mean_tool_jaccard: float = Field(ge=0, le=1)
    mean_evidence_jaccard: float = Field(ge=0, le=1)
    adjudication_rate: float = Field(ge=0, le=1)


class AnnotationAgreementReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_count: int = Field(ge=1)
    label_agreement_rate: float = Field(ge=0, le=1)
    mean_tool_jaccard: float = Field(ge=0, le=1)
    mean_evidence_jaccard: float = Field(ge=0, le=1)
    mean_confidence_delta: float = Field(ge=0, le=1)
    label_kappa: float = Field(ge=-1, le=1)
    adjudication_rate: float = Field(ge=0, le=1)
    fault_type_summaries: tuple[FaultTypeAgreementSummary, ...] = ()
    pairs: tuple[AnnotationDisagreement, ...] = ()

    def to_markdown(self) -> str:
        lines = [
            "# Phase 8A 标注一致性报告",
            "",
            "- 数据性质：`synthetic_protocol_fixture`（不冒充真实专家标注）",
            f"- 任务数：`{self.task_count}`",
            f"- 标签一致率：`{self.label_agreement_rate:.4f}`",
            f"- 工具 Jaccard：`{self.mean_tool_jaccard:.4f}`",
            f"- Evidence Jaccard：`{self.mean_evidence_jaccard:.4f}`",
            f"- Cohen's kappa：`{self.label_kappa:.4f}`",
            f"- 待裁决率：`{self.adjudication_rate:.4f}`",
            "",
            "## 故障域",
            "",
            (
                "| Fault type | Cases | Label agreement | Tool Jaccard | "
                "Evidence Jaccard | Adjudication |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for item in self.fault_type_summaries:
            lines.append(
                f"| {item.fault_type.value} | {item.task_count} | "
                f"{item.label_agreement_rate:.4f} | {item.mean_tool_jaccard:.4f} | "
                f"{item.mean_evidence_jaccard:.4f} | {item.adjudication_rate:.4f} |"
            )
        return "\n".join(lines) + "\n"
class AdjudicationDecision(BaseModel):
    """显式裁决记录；裁决本身仍不修改任何数据集文件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adjudication_id: str = Field(default_factory=lambda: new_id("adjudication"))
    task_id: str = Field(min_length=1)
    first_annotation_id: str = Field(min_length=1)
    second_annotation_id: str = Field(min_length=1)
    adjudicator: str = Field(min_length=1)
    action: AdjudicationAction
    final_candidate_label: str | None = None
    final_tools: tuple[str, ...] = ()
    final_evidence_types: tuple[EvidenceType, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _redact_and_validate_shape(self) -> AdjudicationDecision:
        adjudicator, _ = redact_text(self.adjudicator)
        rationale, _ = redact_text(self.rationale)
        object.__setattr__(self, "adjudicator", adjudicator)
        object.__setattr__(self, "rationale", rationale)
        if self.action is AdjudicationAction.APPROVE and (
            not self.final_candidate_label
            or not self.final_tools
            or not self.final_evidence_types
        ):
            raise ValueError("approve 裁决必须给出完整标签、工具和 Evidence")
        if self.action is not AdjudicationAction.APPROVE and (
            self.final_candidate_label or self.final_tools or self.final_evidence_types
        ):
            raise ValueError("非 approve 裁决不得携带最终标准答案")
        return self


class DatasetAdmissionCandidate(BaseModel):
    """裁决后的只读准入候选；它不是 DatasetCase，也没有写盘能力。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(default_factory=lambda: new_id("dataset_candidate"))
    task_id: str
    source_case_id: str
    fault_type: SecurityFaultType
    proposed_candidate_label: str
    proposed_expected_tools: tuple[str, ...]
    proposed_required_evidence_types: tuple[EvidenceType, ...]
    annotation_ids: tuple[str, str]
    adjudication_id: str
    adjudicator: str
    rationale: str
    status: str = "candidate"


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
    try:
        task = AnnotationTask.model_validate(task.model_dump(mode="python"))
        annotation = BlindAnnotation.model_validate(annotation.model_dump(mode="python"))
    except ValueError as exc:
        raise AnnotationProtocolError("盲标任务或标注意见未通过协议复验") from exc
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
    if first.annotation_id == second.annotation_id:
        raise AnnotationProtocolError("两份盲标意见必须具有不同 annotation_id")
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
        case_id=task.case_id,
        fault_type=task.fault_type,
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
    by_fault_type: dict[SecurityFaultType, list[AnnotationDisagreement]] = {}
    for item in disagreements:
        by_fault_type.setdefault(item.fault_type, []).append(item)
    return AnnotationAgreementReport(
        task_count=len(disagreements),
        label_agreement_rate=_mean(item.label_agrees for item in disagreements),
        mean_tool_jaccard=_mean(item.tool_jaccard for item in disagreements),
        mean_evidence_jaccard=_mean(item.evidence_jaccard for item in disagreements),
        mean_confidence_delta=_mean(item.confidence_delta for item in disagreements),
        label_kappa=_cohen_kappa(disagreements),
        adjudication_rate=_mean(item.requires_adjudication for item in disagreements),
        fault_type_summaries=tuple(
            _fault_type_summary(fault_type, tuple(items))
            for fault_type, items in sorted(by_fault_type.items(), key=lambda pair: pair[0].value)
        ),
        pairs=tuple(disagreements),
    )


def adjudicate_annotations(
    task: AnnotationTask,
    first: BlindAnnotation,
    second: BlindAnnotation,
    decision: AdjudicationDecision,
) -> DatasetAdmissionCandidate:
    """执行显式裁决并返回准入候选；拒绝或待修订不会产生候选。"""
    try:
        task = AnnotationTask.model_validate(task.model_dump(mode="python"))
        decision = AdjudicationDecision.model_validate(decision.model_dump(mode="python"))
    except ValueError as exc:
        raise AnnotationProtocolError("裁决记录未通过协议复验") from exc
    first = validate_blind_annotation(task, first)
    second = validate_blind_annotation(task, second)
    compare_blind_annotations(task, first, second)
    expected_ids = {first.annotation_id, second.annotation_id}
    decision_ids = {decision.first_annotation_id, decision.second_annotation_id}
    if decision.task_id != task.task_id or decision_ids != expected_ids:
        raise AnnotationProtocolError("裁决记录与盲标任务或两份标注意见不匹配")
    if decision.adjudicator in {first.reviewer, second.reviewer}:
        raise AnnotationProtocolError("裁决人必须独立于两名盲标员")
    if decision.action is not AdjudicationAction.APPROVE:
        raise AnnotationProtocolError("只有显式 approve 裁决才能生成准入候选")
    final_label = decision.final_candidate_label
    if final_label is None or final_label not in task.candidate_label_options:
        raise AnnotationProtocolError("裁决标签不在当前故障域目录")
    if not set(decision.final_tools).issubset(task.allowed_tools):
        raise AnnotationProtocolError("裁决工具不在任务允许目录")
    if not set(decision.final_evidence_types).issubset(task.allowed_evidence_types):
        raise AnnotationProtocolError("裁决 Evidence 不在标准目录")
    if len(set(decision.final_tools)) != len(decision.final_tools):
        raise AnnotationProtocolError("裁决工具不允许重复")
    if len(set(decision.final_evidence_types)) != len(decision.final_evidence_types):
        raise AnnotationProtocolError("裁决 Evidence 不允许重复")
    return DatasetAdmissionCandidate(
        task_id=task.task_id,
        source_case_id=task.case_id,
        fault_type=task.fault_type,
        proposed_candidate_label=final_label,
        proposed_expected_tools=decision.final_tools,
        proposed_required_evidence_types=decision.final_evidence_types,
        annotation_ids=(first.annotation_id, second.annotation_id),
        adjudication_id=decision.adjudication_id,
        adjudicator=decision.adjudicator,
        rationale=decision.rationale,
    )


def _jaccard(first: Sequence[object], second: Sequence[object]) -> float:
    left, right = set(first), set(second)
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _fault_type_summary(
    fault_type: SecurityFaultType,
    items: tuple[AnnotationDisagreement, ...],
) -> FaultTypeAgreementSummary:
    return FaultTypeAgreementSummary(
        fault_type=fault_type,
        task_count=len(items),
        label_agreement_rate=_mean(item.label_agrees for item in items),
        mean_tool_jaccard=_mean(item.tool_jaccard for item in items),
        mean_evidence_jaccard=_mean(item.evidence_jaccard for item in items),
        adjudication_rate=_mean(item.requires_adjudication for item in items),
    )


def write_annotation_agreement_report(
    report: AnnotationAgreementReport, output_directory: Path
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "phase8-annotation-agreement.json"
    markdown_path = output_directory / "phase8-annotation-agreement.md"
    _atomic_write(json_path, report.model_dump_json(indent=2))
    _atomic_write(markdown_path, report.to_markdown())
    return json_path, markdown_path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


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
