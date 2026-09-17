"""Phase 7C 版本基线、单变量比较与发布门禁。"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime
from math import isclose
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.redaction import redact_mapping
from security_diagnosis_harness.evaluation.dataset import DatasetCase, DatasetSplit
from security_diagnosis_harness.evaluation.grader import SuiteGrade, _macro_f1


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


class AuthenticatedEvaluationRun(BaseModel):
    """Evaluation run authenticated by the trusted grading boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: EvaluationRun
    auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def issue(cls, run: EvaluationRun, integrity_key: bytes) -> AuthenticatedEvaluationRun:
        _require_integrity_key(integrity_key)
        return cls(run=run, auth_tag=_run_auth_tag(run, integrity_key))

    def verify(self, integrity_key: bytes) -> EvaluationRun:
        _require_integrity_key(integrity_key)
        if not hmac.compare_digest(self.auth_tag, _run_auth_tag(self.run, integrity_key)):
            raise ComparisonConfigurationError("评测运行认证失败")
        return self.run


class AuthenticatedDatasetCases(BaseModel):
    """Dataset labels authenticated by the controlled dataset-loading boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_name: str = Field(min_length=1)
    cases: tuple[DatasetCase, ...]
    auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def issue(
        cls,
        dataset_name: str,
        cases: tuple[DatasetCase, ...],
        integrity_key: bytes,
    ) -> AuthenticatedDatasetCases:
        _require_integrity_key(integrity_key)
        payload = {
            "dataset_name": dataset_name,
            "cases": [case.model_dump(mode="json") for case in cases],
        }
        tag = hmac.new(
            integrity_key, canonical_json(payload).encode(), hashlib.sha256
        ).hexdigest()
        return cls(dataset_name=dataset_name, cases=cases, auth_tag=tag)

    def verify(self, integrity_key: bytes) -> tuple[DatasetCase, ...]:
        trusted = self.issue(self.dataset_name, self.cases, integrity_key)
        if not hmac.compare_digest(self.auth_tag, trusted.auth_tag):
            raise ComparisonConfigurationError("评测数据集认证失败")
        return self.cases


class PublishedDatasetAnchor(BaseModel):
    """Pre-signed dataset anchor bound to one governed release identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0.0"
    release_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor: AuthenticatedDatasetCases
    auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def issue(
        cls,
        release_id: str,
        anchor: AuthenticatedDatasetCases,
        integrity_key: bytes,
    ) -> PublishedDatasetAnchor:
        _require_integrity_key(integrity_key)
        payload = {"schema_version": "1.0.0", "release_id": release_id,
                   "anchor": anchor.model_dump(mode="json")}
        tag = hmac.new(
            integrity_key, canonical_json(payload).encode(), hashlib.sha256
        ).hexdigest()
        return cls(release_id=release_id, anchor=anchor, auth_tag=tag)

    def verify(self, integrity_key: bytes) -> AuthenticatedDatasetCases:
        trusted = self.issue(self.release_id, self.anchor, integrity_key)
        if not hmac.compare_digest(self.auth_tag, trusted.auth_tag):
            raise ComparisonConfigurationError("发布数据集 Anchor 认证失败")
        self.anchor.verify(integrity_key)
        return self.anchor


def _require_integrity_key(integrity_key: bytes) -> None:
    if len(integrity_key) < 32:
        raise ComparisonConfigurationError("评测运行完整性密钥至少需要 32 字节")


def _run_auth_tag(run: EvaluationRun, integrity_key: bytes) -> str:
    return hmac.new(
        integrity_key,
        canonical_json(run.model_dump(mode="json")).encode(),
        hashlib.sha256,
    ).hexdigest()


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
    baseline: AuthenticatedEvaluationRun,
    candidate: AuthenticatedEvaluationRun,
    policy: GatePolicy | None = None,
    *,
    dataset_cases: AuthenticatedDatasetCases | None = None,
    integrity_key: bytes,
) -> GateReport:
    """在控制变量一致时比较两个版本；Candidate 的 P0 永远阻塞。

    ``dataset_cases`` 是版本门禁的可信锚，必须显式传入受控 DatasetCase 集合。门禁不信任评分
    产物自带的 ``CaseGrade.expected_candidate``，而是用它交叉复核并重算
    ``candidate_correct`` / ``candidate_accuracy`` / ``candidate_macro_f1``。
    缺失时 fail-closed（禁止隐式信任或搜索任意路径）。
    """
    if dataset_cases is None:
        raise ComparisonConfigurationError(
            "版本门禁必须显式提供受控 dataset_cases，"
            "禁止信任评分产物自带的 expected_candidate"
        )
    baseline_run = baseline.verify(integrity_key)
    candidate_run = candidate.verify(integrity_key)
    if (
        baseline_run.identity.controlled_variables()
        != candidate_run.identity.controlled_variables()
    ):
        raise ComparisonConfigurationError("除 code_commit 外的评测变量必须完全一致")
    if baseline_run.run_id == candidate_run.run_id:
        raise ComparisonConfigurationError("Baseline 与 Candidate run_id 必须不同")
    if baseline_run.identity.code_commit == candidate_run.identity.code_commit:
        raise ComparisonConfigurationError("Baseline 与 Candidate 必须来自不同代码 commit")
    baseline_cases = {item.case_id: item for item in baseline_run.grade.cases}
    candidate_cases = {item.case_id: item for item in candidate_run.grade.cases}
    if set(baseline_cases) != set(candidate_cases):
        raise ComparisonConfigurationError("Baseline 与 Candidate 的案例集合必须一致")
    trusted_cases = dataset_cases.verify(integrity_key)
    if dataset_cases.dataset_name != baseline_run.identity.dataset_name:
        raise ComparisonConfigurationError("认证数据集名称与评测运行不一致")
    expected_candidates = _validate_dataset_anchor(baseline_run, trusted_cases)
    _validate_dataset_anchor(candidate_run, trusted_cases)
    _validate_grade_consistency(baseline_run, expected_candidates)
    _validate_grade_consistency(candidate_run, expected_candidates)

    policy = policy or GatePolicy()
    deltas = tuple(
        _metric_delta(baseline_run, candidate_run, policy, metric, tolerance)
        for metric, tolerance in CORE_METRICS
    )

    case_diffs = tuple(
        _case_diff(case_id, baseline_cases[case_id], candidate_cases[case_id])
        for case_id in sorted(baseline_cases)
    )
    # 不信任可由外部文件提供的汇总字段，P0 必须从逐案例结果重新计算。
    p0_count = sum(item.p0_blocked for item in candidate_run.grade.cases)
    reasons = [f"Candidate 存在 {p0_count} 个 P0 失败"] if p0_count else []
    reasons.extend(f"核心指标退化: {item.metric}" for item in deltas if item.regressed)
    return GateReport(
        baseline_run_id=baseline_run.run_id,
        candidate_run_id=candidate_run.run_id,
        baseline_hash=baseline_run.content_hash(),
        candidate_hash=candidate_run.content_hash(),
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


_AVERAGED_SUITE_FIELDS: tuple[tuple[str, str], ...] = (
    ("task_completion_rate", "task_completed"),
    ("controlled_degradation_rate", "controlled_degradation"),
    ("candidate_accuracy", "candidate_correct"),
    ("citation_compliance", "citation_compliance"),
    ("unsupported_claim_rate", "unsupported_claim_rate"),
    ("tool_precision", "tool_precision"),
    ("tool_recall", "tool_recall"),
    ("parameter_valid_rate", "parameter_valid_rate"),
    ("fault_type_match_rate", "fault_type_match_rate"),
    ("repeated_failed_call_rate", "repeated_failed_call_rate"),
    ("evidence_coverage", "evidence_coverage"),
    ("evidence_ownership_rate", "evidence_ownership_rate"),
    ("evidence_reliability_rate", "evidence_reliability_rate"),
    ("average_rounds", "rounds"),
    ("average_tool_calls", "tool_call_count"),
    ("average_latency_ms", "latency_ms"),
)


def _validate_grade_consistency(
    run: EvaluationRun,
    expected_candidates: Mapping[str, str],
) -> None:
    """拒绝逐案例与汇总指标不一致、或与受控数据集不一致的外部评测产物。"""
    cases = run.grade.cases
    metrics = run.grade.metrics
    if metrics.total != len(cases):
        raise ComparisonConfigurationError(f"{run.run_id} 的 total 与逐案例数量不一致")

    if set(expected_candidates) != {case.case_id for case in cases}:
        raise ComparisonConfigurationError(
            f"{run.run_id} 的 case_id 集合与受控数据集不一致"
        )

    for case in cases:
        expected_p0 = any(item.level.value == "p0" for item in case.findings)
        expected_passed = not case.findings
        if case.p0_blocked != expected_p0 or case.passed != expected_passed:
            raise ComparisonConfigurationError(
                f"{run.run_id} 的案例 {case.case_id} 状态与 findings 不一致"
            )
        trusted_expected = expected_candidates[case.case_id]
        if case.expected_candidate != trusted_expected:
            raise ComparisonConfigurationError(
                f"{run.run_id} 的案例 {case.case_id} expected_candidate 与受控数据集不一致"
            )
        # candidate_correct 必须由受控 expected 与产物 predicted 重算，而非自报。
        expected_correct = float(case.predicted_candidate == trusted_expected)
        _require_metric(
            run.run_id,
            f"{case.case_id}.candidate_correct",
            case.metrics.candidate_correct,
            expected_correct,
        )

    expected_pass_rate = _average(float(item.passed) for item in cases)
    _require_metric(run.run_id, "pass_rate", metrics.pass_rate, expected_pass_rate)
    for suite_field, case_field in _AVERAGED_SUITE_FIELDS:
        expected = _average(float(getattr(item.metrics, case_field)) for item in cases)
        _require_metric(run.run_id, suite_field, float(getattr(metrics, suite_field)), expected)
    # candidate_macro_f1 必须由受控 expected 与产物 predicted 重算。
    expected_macro_f1 = _macro_f1(
        [expected_candidates[case.case_id] for case in cases],
        [case.predicted_candidate for case in cases],
    )
    _require_metric(
        run.run_id, "candidate_macro_f1", metrics.candidate_macro_f1, expected_macro_f1
    )
    expected_cost = sum(item.metrics.estimated_cost for item in cases)
    _require_metric(run.run_id, "estimated_cost", metrics.estimated_cost, expected_cost)


def _validate_dataset_anchor(
    run: EvaluationRun, dataset_cases: tuple[DatasetCase, ...]
) -> dict[str, str]:
    if not dataset_cases:
        raise ComparisonConfigurationError("受控 DatasetCase 集合不能为空")
    if any(
        case.dataset_version != run.identity.dataset_version
        or case.split is not run.identity.split
        for case in dataset_cases
    ):
        raise ComparisonConfigurationError("DatasetCase 与运行的数据集版本或 split 不一致")
    expected = {case.case_id: case.expected_candidate for case in dataset_cases}
    if len(expected) != len(dataset_cases):
        raise ComparisonConfigurationError("受控 DatasetCase 集合包含重复 case_id")
    return expected


def _average(values: Any) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _require_metric(run_id: str, name: str, actual: float, expected: float) -> None:
    if not isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9):
        raise ComparisonConfigurationError(
            f"{run_id} 的汇总指标 {name} 与逐案例结果不一致"
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
