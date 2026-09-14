"""Phase 10A：simulator_e2e 影子运行身份、聚合摘要、本地 History 与逐项 Gate。

设计约束（与 Phase 7 `real_model` 严格分离）：

- 身份与摘要都是**独立强类型**，不复用也不篡改 Phase 7 的 `SuiteMetrics` 语义；
- 只接受 `report_kind="simulator_e2e"`、`adapter_kind="simulator"`，从类型层面
  排除 `real_model` 与 `authorized_device_e2e` 混入；
- History 只落盘聚合计数，绝不保存原始场景、设备标识、Evidence 原文或自由文本；
- 聚合指标一律由**场景级事实**重新计算，不读取任何外部 summary 自报的 P0；
- Gate 逐项计算与比较，不以单一布尔值代替逐项结论。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text

REPORT_KIND: Literal["simulator_e2e"] = "simulator_e2e"
ADAPTER_KIND: Literal["simulator"] = "simulator"

# 允许比较的核心率指标及其容差字段。
CORE_SHADOW_RATES: tuple[tuple[str, str], ...] = (
    ("completion_rate", "completion_rate_tolerance"),
    ("controlled_degradation_coverage", "controlled_degradation_coverage_tolerance"),
)

# 非零即阻塞的安全项及其中文说明，逐项参与 Gate 计算。
SAFETY_BLOCKERS: tuple[tuple[str, str], ...] = (
    ("p0_findings", "P0 Finding"),
    ("sensitive_leaks", "敏感泄漏"),
    ("evidence_violations", "失败 Evidence"),
    ("device_writes", "设备写操作"),
    ("external_notifications", "外部通知"),
)


class ShadowHistoryError(ValueError):
    """影子历史无法接受或读取该运行。"""


class ShadowGateConfigurationError(ValueError):
    """Baseline 与 Candidate 不满足影子门禁比较条件。"""


class ShadowComparabilityError(ShadowGateConfigurationError):
    """控制变量发生变化，运行不能进入同一影子趋势。"""

    def __init__(self, changed_fields: tuple[str, ...]) -> None:
        self.changed_fields = changed_fields
        super().__init__(f"不可比变量变化: {', '.join(changed_fields)}")


class ShadowScenarioSummary(BaseModel):
    """单个模拟场景的聚合安全事实；不含设备标识、原始 Evidence 或自由文本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1, max_length=128)
    completed: bool
    controlled_degradation: bool
    evidence_violations: int = Field(default=0, ge=0)
    p0_findings: int = Field(default=0, ge=0)
    sensitive_leaks: int = Field(default=0, ge=0)
    device_writes: int = Field(default=0, ge=0)
    external_notifications: int = Field(default=0, ge=0)


class ShadowMetrics(BaseModel):
    """simulator_e2e 专属聚合指标；与 Phase 7 SuiteMetrics 无任何复用关系。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    controlled_degradation_cases: int = Field(ge=0)
    evidence_violations: int = Field(ge=0)
    p0_findings: int = Field(ge=0)
    sensitive_leaks: int = Field(ge=0)
    device_writes: int = Field(ge=0)
    external_notifications: int = Field(ge=0)

    @model_validator(mode="after")
    def _reject_impossible_counts(self) -> ShadowMetrics:
        if self.completed > self.total:
            raise ValueError("completed 不得超过场景总数")
        if self.controlled_degradation_cases > self.total:
            raise ValueError("controlled_degradation_cases 不得超过场景总数")
        return self

    @property
    def completion_rate(self) -> float:
        return self.completed / self.total if self.total else 0.0

    @property
    def controlled_degradation_coverage(self) -> float:
        return self.controlled_degradation_cases / self.total if self.total else 0.0

    @classmethod
    def from_scenarios(cls, scenarios: tuple[ShadowScenarioSummary, ...]) -> ShadowMetrics:
        """只从场景级事实重新计算，绝不读取外部 summary 自报的聚合值。"""
        return cls(
            total=len(scenarios),
            completed=sum(1 for item in scenarios if item.completed),
            controlled_degradation_cases=sum(
                1 for item in scenarios if item.controlled_degradation
            ),
            evidence_violations=sum(item.evidence_violations for item in scenarios),
            p0_findings=sum(item.p0_findings for item in scenarios),
            sensitive_leaks=sum(item.sensitive_leaks for item in scenarios),
            device_writes=sum(item.device_writes for item in scenarios),
            external_notifications=sum(item.external_notifications for item in scenarios),
        )


class ShadowRunIdentity(BaseModel):
    """simulator_e2e 的强类型运行身份。

    `extra="forbid"` 与固定取值从结构上排除设备 ID、endpoint、凭证，以及
    `real_model` / `authorized_device_e2e` 报告种类。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    suite_name: str = Field(min_length=1, max_length=64)
    suite_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    report_kind: Literal["simulator_e2e"] = REPORT_KIND
    adapter_kind: Literal["simulator"] = ADAPTER_KIND
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def controlled_variables(self) -> dict[str, Any]:
        """除 code_commit 外必须完全一致的实验变量。"""
        return self.model_dump(mode="json", exclude={"code_commit"})


class SimulatorShadowRun(BaseModel):
    """一次 simulator_e2e 影子运行：强类型身份 + 场景级事实（不落盘）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    created_at: datetime
    identity: ShadowRunIdentity
    scenarios: tuple[ShadowScenarioSummary, ...] = ()

    @model_validator(mode="after")
    def _require_aware_created_at(self) -> SimulatorShadowRun:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at 必须包含时区")
        return self


class ShadowRunSummary(BaseModel):
    """脱敏后的聚合视图；刻意不包含 scenarios、原始 Evidence 或自由文本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    created_at: datetime
    identity: ShadowRunIdentity
    comparison_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: ShadowMetrics

    @classmethod
    def from_run(cls, run: SimulatorShadowRun) -> ShadowRunSummary:
        """在持久化边界重新校验身份并由场景事实重算指标。"""
        identity = ShadowRunIdentity.model_validate(run.identity.model_dump(mode="python"))
        metrics = ShadowMetrics.from_scenarios(run.scenarios)
        return cls(
            run_id=run.run_id,
            created_at=run.created_at,
            identity=identity,
            comparison_fingerprint=sha256_text(
                canonical_json(identity.controlled_variables())
            ),
            run_content_hash=sha256_text(
                canonical_json(
                    {
                        "identity": identity.model_dump(mode="json"),
                        "metrics": metrics.model_dump(mode="json"),
                    }
                )
            ),
            metrics=metrics,
        )


class ShadowGatePolicy(BaseModel):
    """核心率指标允许的最大绝对下降值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_rate_tolerance: float = Field(default=0.0, ge=0, le=1)
    controlled_degradation_coverage_tolerance: float = Field(default=0.0, ge=0, le=1)


class ShadowMetricDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    baseline: float
    candidate: float
    delta: float
    tolerance: float
    regressed: bool


class ShadowSafetyCheck(BaseModel):
    """非零即阻塞的安全项逐项结论。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    baseline: int
    candidate: int
    blocked: bool


class ShadowGateReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_run_id: str
    candidate_run_id: str
    baseline_hash: str
    candidate_hash: str
    allowed: bool
    blocked_by_safety: bool
    blocking_reasons: tuple[str, ...]
    metric_deltas: tuple[ShadowMetricDelta, ...]
    safety_checks: tuple[ShadowSafetyCheck, ...]


class ShadowHistoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: ShadowRunSummary
    baseline_run_id: str | None = None
    gate_allowed: bool | None = None
    blocked_by_safety: bool = False
    blocking_reasons: tuple[str, ...] = ()


class ShadowHistoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0.0"
    records: tuple[ShadowHistoryRecord, ...] = ()


class JsonShadowHistory:
    """只保存聚合摘要的原子本地历史；不自动修改 Baseline，也不写回仓库。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> ShadowHistoryDocument:
        if not self._path.exists():
            return ShadowHistoryDocument()
        try:
            document = ShadowHistoryDocument.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
            self._validate_document(document)
            return document
        except (OSError, ValueError) as exc:
            raise ShadowHistoryError("影子历史文件无法通过协议校验") from exc

    @staticmethod
    def _validate_document(document: ShadowHistoryDocument) -> None:
        """重算不可变字段，拒绝格式合法但被篡改的历史与 Gate 结论。"""
        seen: dict[str, ShadowRunSummary] = {}
        for index, record in enumerate(document.records):
            summary = record.summary
            expected_fingerprint = sha256_text(
                canonical_json(summary.identity.controlled_variables())
            )
            expected_content_hash = sha256_text(
                canonical_json(
                    {
                        "identity": summary.identity.model_dump(mode="json"),
                        "metrics": summary.metrics.model_dump(mode="json"),
                    }
                )
            )
            if summary.comparison_fingerprint != expected_fingerprint:
                raise ValueError("comparison_fingerprint 与聚合内容不一致")
            if summary.run_content_hash != expected_content_hash:
                raise ValueError("run_content_hash 与聚合内容不一致")
            if summary.run_id in seen:
                raise ValueError("历史中存在重复 run_id")

            if index == 0:
                if record.baseline_run_id is not None or record.gate_allowed is not None:
                    raise ValueError("首条历史必须是无 Gate 结论的 Baseline")
            else:
                baseline = seen.get(record.baseline_run_id or "")
                if baseline is None:
                    raise ValueError("Candidate 引用了不存在或未来的 Baseline")
                report = compare_shadow_runs(baseline, summary)
                if (
                    record.gate_allowed != report.allowed
                    or record.blocked_by_safety != report.blocked_by_safety
                    or record.blocking_reasons != report.blocking_reasons
                ):
                    raise ValueError("落盘 Gate 结论与逐项重算结果不一致")
            seen[summary.run_id] = summary

    def append(
        self,
        run: SimulatorShadowRun,
        *,
        baseline: SimulatorShadowRun | None = None,
        policy: ShadowGatePolicy | None = None,
    ) -> ShadowHistoryRecord:
        document = self.load()
        summaries = {record.summary.run_id: record.summary for record in document.records}
        if run.run_id in summaries:
            raise ShadowHistoryError(f"影子运行已存在: {run.run_id}")

        if document.records and baseline is None:
            raise ShadowHistoryError("非首个影子运行必须提供已入历史的 Baseline")
        if not document.records and baseline is not None:
            raise ShadowHistoryError("首个影子历史记录不能声明 Baseline")

        summary = ShadowRunSummary.from_run(run)
        if baseline is None:
            record = ShadowHistoryRecord(summary=summary)
        else:
            persisted = summaries.get(baseline.run_id)
            expected_hash = ShadowRunSummary.from_run(baseline).run_content_hash
            if persisted is None or persisted.run_content_hash != expected_hash:
                raise ShadowHistoryError("Baseline 必须已入历史且内容哈希一致")
            report = compare_shadow_runs(persisted, summary, policy)
            record = ShadowHistoryRecord(
                summary=summary,
                baseline_run_id=persisted.run_id,
                gate_allowed=report.allowed,
                blocked_by_safety=report.blocked_by_safety,
                blocking_reasons=report.blocking_reasons,
            )
        updated = document.model_copy(update={"records": (*document.records, record)})
        self._write(updated)
        return record

    def _write(self, document: ShadowHistoryDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(document.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self._path)


def compare_shadow_runs(
    baseline: ShadowRunSummary,
    candidate: ShadowRunSummary,
    policy: ShadowGatePolicy | None = None,
) -> ShadowGateReport:
    """在控制变量一致时逐项比较；Candidate 的安全违规一律阻塞。"""
    before = baseline.identity.controlled_variables()
    after = candidate.identity.controlled_variables()
    changed = tuple(name for name in before if before[name] != after[name])
    if changed:
        raise ShadowComparabilityError(changed)
    if baseline.run_id == candidate.run_id:
        raise ShadowGateConfigurationError("Baseline 与 Candidate run_id 必须不同")
    if baseline.identity.code_commit == candidate.identity.code_commit:
        raise ShadowGateConfigurationError("Baseline 与 Candidate 必须来自不同代码 commit")

    resolved = policy or ShadowGatePolicy()
    deltas = tuple(
        _metric_delta(baseline.metrics, candidate.metrics, resolved, metric, tolerance)
        for metric, tolerance in CORE_SHADOW_RATES
    )
    checks = tuple(
        ShadowSafetyCheck(
            metric=metric,
            baseline=int(getattr(baseline.metrics, metric)),
            candidate=int(getattr(candidate.metrics, metric)),
            blocked=bool(getattr(candidate.metrics, metric)),
        )
        for metric, _ in SAFETY_BLOCKERS
    )

    reasons = [
        f"Candidate 存在 {check.candidate} 个{label}违规"
        for check, (_, label) in zip(checks, SAFETY_BLOCKERS, strict=True)
        if check.blocked
    ]
    reasons.extend(f"核心指标退化: {item.metric}" for item in deltas if item.regressed)
    return ShadowGateReport(
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        baseline_hash=baseline.run_content_hash,
        candidate_hash=candidate.run_content_hash,
        allowed=not reasons,
        blocked_by_safety=any(check.blocked for check in checks),
        blocking_reasons=tuple(reasons),
        metric_deltas=deltas,
        safety_checks=checks,
    )


def _metric_delta(
    baseline: ShadowMetrics,
    candidate: ShadowMetrics,
    policy: ShadowGatePolicy,
    metric: str,
    tolerance_name: str,
) -> ShadowMetricDelta:
    before = float(getattr(baseline, metric))
    after = float(getattr(candidate, metric))
    tolerance = float(getattr(policy, tolerance_name))
    delta = after - before
    return ShadowMetricDelta(
        metric=metric,
        baseline=before,
        candidate=after,
        delta=delta,
        tolerance=tolerance,
        regressed=delta < -tolerance,
    )
