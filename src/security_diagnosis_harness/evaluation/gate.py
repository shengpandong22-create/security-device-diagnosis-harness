"""Phase 7C 版本基线、单变量比较与发布门禁。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.redaction import redact_mapping
from security_diagnosis_harness.evaluation.dataset import DatasetSplit
from security_diagnosis_harness.evaluation.grader import SuiteGrade


class ComparisonConfigurationError(ValueError):
    """Baseline 与 Candidate 不满足单变量比较条件。"""


class RunIdentity(BaseModel):
    """一次评测运行的可复现身份，不保存任何密钥。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    split: DatasetSplit
    runner_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_sensitive_metadata(self) -> RunIdentity:
        for field_name in ("model_parameters", "environment"):
            value = getattr(self, field_name)
            redacted, changed = redact_mapping(value)
            if changed or redacted != value:
                raise ValueError(f"{field_name} 不得包含敏感信息")
        return self

    def controlled_variables(self) -> dict[str, Any]:
        """除代码版本外，所有必须固定的实验变量。"""
        return self.model_dump(mode="json", exclude={"code_commit"})


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    created_at: datetime
    identity: RunIdentity
    grade: SuiteGrade

    def content_hash(self) -> str:
        return sha256_text(canonical_json(self.model_dump(mode="json")))


class GatePolicy(BaseModel):
    """核心指标允许的最大绝对下降值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_completion_tolerance: float = Field(default=0.0, ge=0, le=1)
    candidate_accuracy_tolerance: float = Field(default=0.0, ge=0, le=1)
    candidate_macro_f1_tolerance: float = Field(default=0.0, ge=0, le=1)
    citation_compliance_tolerance: float = Field(default=0.0, ge=0, le=1)
    tool_recall_tolerance: float = Field(default=0.05, ge=0, le=1)
    evidence_coverage_tolerance: float = Field(default=0.0, ge=0, le=1)


class MetricDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    baseline: float
    candidate: float
    delta: float
    tolerance: float
    regressed: bool


class CaseDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    baseline_passed: bool
    candidate_passed: bool
    fixed_findings: tuple[str, ...]
    new_findings: tuple[str, ...]


class GateReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_run_id: str
    candidate_run_id: str
    baseline_hash: str
    candidate_hash: str
    allowed: bool
    blocked_by_p0: bool
    blocking_reasons: tuple[str, ...]
    metric_deltas: tuple[MetricDelta, ...]
    case_diffs: tuple[CaseDiff, ...]

    def to_markdown(self) -> str:
        lines = [
            "# Phase 7 版本评测差异报告",
            "",
            f"- Baseline: `{self.baseline_run_id}`",
            f"- Candidate: `{self.candidate_run_id}`",
            f"- 发布门禁: `{'PASS' if self.allowed else 'BLOCKED'}`",
            f"- P0 阻塞: `{str(self.blocked_by_p0).lower()}`",
            "",
            "## 指标差异",
            "",
            "| 指标 | Baseline | Candidate | Delta | 容差 | 退化 |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for item in self.metric_deltas:
            lines.append(
                f"| {item.metric} | {item.baseline:.4f} | {item.candidate:.4f} "
                f"| {item.delta:+.4f} | {item.tolerance:.4f} | "
                f"{'是' if item.regressed else '否'} |"
            )
        lines.extend(["", "## 逐案例变化", ""])
        for item in self.case_diffs:
            lines.extend(
                [
                    f"### {item.case_id}",
                    f"- 通过状态：`{item.baseline_passed}` → `{item.candidate_passed}`",
                    f"- 新增失败：{', '.join(item.new_findings) or '无'}",
                    f"- 已修复：{', '.join(item.fixed_findings) or '无'}",
                    "",
                ]
            )
        if self.blocking_reasons:
            lines.extend(["## 阻塞原因", "", *[f"- {item}" for item in self.blocking_reasons]])
        return "\n".join(lines).rstrip() + "\n"


CORE_METRICS: tuple[tuple[str, str], ...] = (
    ("task_completion_rate", "task_completion_tolerance"),
    ("candidate_accuracy", "candidate_accuracy_tolerance"),
    ("candidate_macro_f1", "candidate_macro_f1_tolerance"),
    ("citation_compliance", "citation_compliance_tolerance"),
    ("tool_recall", "tool_recall_tolerance"),
    ("evidence_coverage", "evidence_coverage_tolerance"),
)


def compare_runs(
    baseline: EvaluationRun,
    candidate: EvaluationRun,
    policy: GatePolicy | None = None,
) -> GateReport:
    """在控制变量一致时比较两个版本；Candidate 的 P0 永远阻塞。"""
    if baseline.identity.controlled_variables() != candidate.identity.controlled_variables():
        raise ComparisonConfigurationError("除 code_commit 外的评测变量必须完全一致")
    if baseline.run_id == candidate.run_id:
        raise ComparisonConfigurationError("Baseline 与 Candidate run_id 必须不同")
    if baseline.identity.code_commit == candidate.identity.code_commit:
        raise ComparisonConfigurationError("Baseline 与 Candidate 必须来自不同代码 commit")

    policy = policy or GatePolicy()
    deltas = tuple(
        _metric_delta(baseline, candidate, policy, metric, tolerance)
        for metric, tolerance in CORE_METRICS
    )
    baseline_cases = {item.case_id: item for item in baseline.grade.cases}
    candidate_cases = {item.case_id: item for item in candidate.grade.cases}
    if set(baseline_cases) != set(candidate_cases):
        raise ComparisonConfigurationError("Baseline 与 Candidate 的案例集合必须一致")

    case_diffs = tuple(
        _case_diff(case_id, baseline_cases[case_id], candidate_cases[case_id])
        for case_id in sorted(baseline_cases)
    )
    # 不信任可由外部文件提供的汇总字段，P0 必须从逐案例结果重新计算。
    p0_count = sum(item.p0_blocked for item in candidate.grade.cases)
    reasons = [f"Candidate 存在 {p0_count} 个 P0 失败"] if p0_count else []
    reasons.extend(f"核心指标退化: {item.metric}" for item in deltas if item.regressed)
    return GateReport(
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        baseline_hash=baseline.content_hash(),
        candidate_hash=candidate.content_hash(),
        allowed=not reasons,
        blocked_by_p0=bool(p0_count),
        blocking_reasons=tuple(reasons),
        metric_deltas=deltas,
        case_diffs=case_diffs,
    )


def write_gate_report(report: GateReport, output_directory: Path) -> tuple[Path, Path]:
    """原子写入 JSON 与 Markdown 报告；不保存原始案例输入。"""
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "phase7-gate-report.json"
    markdown_path = output_directory / "phase7-gate-report.md"
    _atomic_write(json_path, report.model_dump_json(indent=2))
    _atomic_write(markdown_path, report.to_markdown())
    return json_path, markdown_path


def _metric_delta(
    baseline: EvaluationRun,
    candidate: EvaluationRun,
    policy: GatePolicy,
    metric: str,
    tolerance_name: str,
) -> MetricDelta:
    before = float(getattr(baseline.grade.metrics, metric))
    after = float(getattr(candidate.grade.metrics, metric))
    tolerance = float(getattr(policy, tolerance_name))
    delta = after - before
    return MetricDelta(
        metric=metric,
        baseline=before,
        candidate=after,
        delta=delta,
        tolerance=tolerance,
        regressed=delta < -tolerance,
    )


def _case_diff(case_id: str, baseline: Any, candidate: Any) -> CaseDiff:
    before = {item.code for item in baseline.findings}
    after = {item.code for item in candidate.findings}
    return CaseDiff(
        case_id=case_id,
        baseline_passed=baseline.passed,
        candidate_passed=candidate.passed,
        fixed_findings=tuple(sorted(before - after)),
        new_findings=tuple(sorted(after - before)),
    )


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
