"""Phase 8C safe evaluation history, comparable trends, and gate lineage."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.redaction import redact_mapping
from security_diagnosis_harness.evaluation.dataset import DatasetCase, DatasetSplit
from security_diagnosis_harness.evaluation.gate import (
    CORE_METRICS,
    AuthenticatedDatasetCases,
    AuthenticatedEvaluationRun,
    ComparisonConfigurationError,
    EvaluationRun,
    GatePolicy,
    RunIdentity,
    compare_runs,
)
from security_diagnosis_harness.evaluation.grader import SuiteMetrics


class EvaluationHistoryError(ValueError):
    """History cannot accept or read the requested evaluation run."""


class TrendComparabilityError(ComparisonConfigurationError):
    """A run changes controlled variables and cannot enter this trend."""

    def __init__(self, changed_fields: tuple[str, ...]) -> None:
        self.changed_fields = changed_fields
        super().__init__(f"不可比变量变化: {', '.join(changed_fields)}")


class EvaluationRunSummary(BaseModel):
    """Sanitized aggregate-only view; deliberately excludes cases and traces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    created_at: datetime
    code_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    dataset_name: str
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    split: DatasetSplit
    runner_name: str
    model_name: str
    model_parameters: dict[str, Any]
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: SuiteMetrics
    deterministic_p0_count: int = Field(ge=0)
    summary_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _reject_sensitive_metadata(self) -> EvaluationRunSummary:
        redacted, changed = redact_mapping(self.model_parameters)
        if changed or redacted != self.model_parameters:
            raise ValueError("model_parameters 不得包含敏感信息")
        return self

    @classmethod
    def from_run(cls, run: EvaluationRun) -> EvaluationRunSummary:
        # Revalidate at the persistence boundary to block model_copy bypasses.
        identity = RunIdentity.model_validate(run.identity.model_dump(mode="python"))
        p0_count = sum(case.p0_blocked for case in run.grade.cases)
        metrics = run.grade.metrics.model_copy(update={"p0_failure_count": p0_count})
        summary = cls(
            run_id=run.run_id,
            created_at=run.created_at,
            code_commit=identity.code_commit,
            dataset_name=identity.dataset_name,
            dataset_version=identity.dataset_version,
            split=identity.split,
            runner_name=identity.runner_name,
            model_name=identity.model_name,
            model_parameters=identity.model_parameters,
            prompt_hash=identity.prompt_hash,
            configuration_hash=identity.configuration_hash,
            environment_hash=sha256_text(canonical_json(identity.environment)),
            comparison_fingerprint="0" * 64,
            run_content_hash=run.content_hash(),
            metrics=metrics,
            deterministic_p0_count=p0_count,
            summary_content_hash="0" * 64,
        )
        summary = summary.model_copy(
            update={
                "comparison_fingerprint": sha256_text(
                    canonical_json(_controlled_summary_values(summary))
                )
            }
        )
        return summary.model_copy(update={"summary_content_hash": _summary_hash(summary)})


class EvaluationHistoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: EvaluationRunSummary
    baseline_run_id: str | None = None
    gate_allowed: bool | None = None
    blocked_by_p0: bool = False
    blocking_reasons: tuple[str, ...] = ()
    # 非敏感 GatePolicy 快照与其规范化哈希；用于加载期复验策略未被篡改。
    gate_policy: dict[str, Any] | None = None
    gate_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationHistoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "4.0.0"
    records: tuple[EvaluationHistoryRecord, ...] = ()
    document_auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")


#: 当前受支持的评测历史 schema 版本。其它版本明确拒绝（fail-closed），
#: 不做隐式兼容，避免"格式合法但语义不同"的历史被静默接受。
SUPPORTED_HISTORY_SCHEMA_VERSIONS: frozenset[str] = frozenset({"4.0.0"})


def _record_auth_tag(record: EvaluationHistoryRecord, integrity_key: bytes) -> str:
    payload = canonical_json(
        record.model_dump(mode="json", exclude={"record_auth_tag"})
    ).encode()
    return hmac.new(integrity_key, payload, hashlib.sha256).hexdigest()


def _document_auth_tag(
    document: EvaluationHistoryDocument, integrity_key: bytes
) -> str:
    payload = canonical_json(
        document.model_dump(mode="json", exclude={"document_auth_tag"})
    ).encode()
    return hmac.new(integrity_key, payload, hashlib.sha256).hexdigest()


def _summary_hash(value: EvaluationRunSummary | dict[str, Any]) -> str:
    payload = (
        value.model_dump(mode="json", exclude={"summary_content_hash"})
        if isinstance(value, EvaluationRunSummary)
        else value
    )
    return sha256_text(canonical_json(payload))


def _controlled_summary_values(summary: EvaluationRunSummary) -> dict[str, Any]:
    return {
        "dataset_name": summary.dataset_name,
        "dataset_version": summary.dataset_version,
        "split": summary.split.value,
        "runner_name": summary.runner_name,
        "model_name": summary.model_name,
        "model_parameters": summary.model_parameters,
        "prompt_hash": summary.prompt_hash,
        "configuration_hash": summary.configuration_hash,
        "environment_hash": summary.environment_hash,
    }


def _recompute_summary_gate(
    baseline: EvaluationRunSummary,
    candidate: EvaluationRunSummary,
    policy: GatePolicy,
) -> tuple[bool, bool, tuple[str, ...]]:
    blocked_by_p0 = candidate.deterministic_p0_count > 0
    reasons = (
        [f"Candidate 存在 {candidate.deterministic_p0_count} 个 P0 失败"]
        if blocked_by_p0
        else []
    )
    for metric, tolerance_name in CORE_METRICS:
        before = float(getattr(baseline.metrics, metric))
        after = float(getattr(candidate.metrics, metric))
        if after - before < -float(getattr(policy, tolerance_name)):
            reasons.append(f"核心指标退化: {metric}")
    return not reasons, blocked_by_p0, tuple(reasons)


def _validate_document(
    document: EvaluationHistoryDocument, integrity_key: bytes
) -> EvaluationHistoryDocument:
    """复验历史文档的结构不变量；拒绝被篡改或顺序错误的历史。"""
    if document.schema_version not in SUPPORTED_HISTORY_SCHEMA_VERSIONS:
        raise EvaluationHistoryError("不支持的评测历史 schema_version")
    if not hmac.compare_digest(
        document.document_auth_tag, _document_auth_tag(document, integrity_key)
    ):
        raise EvaluationHistoryError("评测历史文档认证失败")
    seen: set[str] = set()
    summaries: dict[str, EvaluationRunSummary] = {}
    for record in document.records:
        expected_auth_tag = _record_auth_tag(record, integrity_key)
        if not hmac.compare_digest(record.record_auth_tag, expected_auth_tag):
            raise EvaluationHistoryError("评测历史记录认证失败")
        summary = record.summary
        if summary.comparison_fingerprint != sha256_text(
            canonical_json(_controlled_summary_values(summary))
        ):
            raise EvaluationHistoryError("comparison_fingerprint 与运行身份不一致")
        if summary.summary_content_hash != _summary_hash(summary):
            raise EvaluationHistoryError("summary_content_hash 与历史摘要不一致")
        if summary.run_id in seen:
            raise EvaluationHistoryError(f"评测历史存在重复 run_id: {summary.run_id}")
        baseline_id = record.baseline_run_id
        if baseline_id is None:
            if record.gate_allowed is not None:
                raise EvaluationHistoryError("基线记录不得携带 Gate 结论")
            if record.blocked_by_p0 or record.blocking_reasons:
                raise EvaluationHistoryError("基线记录不得携带阻塞原因")
        else:
            if baseline_id not in seen:
                raise EvaluationHistoryError("Baseline 必须指向此前已入历史的记录")
            if record.gate_allowed is None:
                raise EvaluationHistoryError("非基线记录必须携带 Gate 结论")
            if record.gate_allowed != (not record.blocking_reasons):
                raise EvaluationHistoryError("Gate 结论与阻塞原因不一致")
            if record.blocked_by_p0 and record.gate_allowed:
                raise EvaluationHistoryError("P0 阻塞记录的 Gate 结论不能为允许")
        if record.gate_policy_hash is not None:
            if record.gate_policy is None:
                raise EvaluationHistoryError("Gate 策略哈希必须绑定策略快照")
            if sha256_text(canonical_json(record.gate_policy)) != record.gate_policy_hash:
                raise EvaluationHistoryError("Gate 策略快照与哈希不一致")
        if baseline_id is not None:
            if record.gate_policy is None:
                raise EvaluationHistoryError("非基线记录必须保存 Gate 策略")
            expected = _recompute_summary_gate(
                summaries[baseline_id], summary, GatePolicy.model_validate(record.gate_policy)
            )
            actual = (record.gate_allowed, record.blocked_by_p0, record.blocking_reasons)
            if actual != expected:
                raise EvaluationHistoryError("Gate 结论与历史摘要重算结果不一致")
        seen.add(summary.run_id)
        summaries[summary.run_id] = summary
    return document


class TrendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    code_commit: str
    created_at: datetime
    baseline_run_id: str | None
    gate_allowed: bool | None
    metrics: dict[str, float]
    deterministic_p0_count: int


class EvaluationTrendReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_fingerprint: str
    dataset_name: str
    dataset_version: str
    model_name: str
    prompt_hash: str
    configuration_hash: str
    points: tuple[TrendPoint, ...]
    blocked_run_ids: tuple[str, ...]

    def to_markdown(self) -> str:
        lines = [
            "# Phase 8C 评测趋势报告", "",
            f"- Dataset: `{self.dataset_name}@{self.dataset_version}`",
            f"- Model: `{self.model_name}`", f"- Prompt hash: `{self.prompt_hash}`",
            f"- Configuration hash: `{self.configuration_hash}`",
            f"- 可比运行数: `{len(self.points)}`", "", "## 趋势", "",
            "| Run | Commit | Baseline | Gate | P0 | Candidate accuracy | Tool recall |",
            "|---|---|---|---|---:|---:|---:|",
        ]
        for point in self.points:
            gate = "BASELINE" if point.gate_allowed is None else (
                "PASS" if point.gate_allowed else "BLOCKED"
            )
            lines.append(
                f"| `{point.run_id}` | `{point.code_commit}` | "
                f"`{point.baseline_run_id or '-'}` | `{gate}` | "
                f"{point.deterministic_p0_count} | "
                f"{point.metrics['candidate_accuracy']:.4f} | "
                f"{point.metrics['tool_recall']:.4f} |"
            )
        return "\n".join(lines) + "\n"


class JsonEvaluationHistory:
    """Atomic local history repository containing summaries, never full runs."""

    def __init__(self, path: Path, *, integrity_key: bytes) -> None:
        if len(integrity_key) < 32:
            raise ValueError("评测历史完整性密钥至少需要 32 字节")
        self._path = path
        self._integrity_key = bytes(integrity_key)

    def _empty_document(self) -> EvaluationHistoryDocument:
        document = EvaluationHistoryDocument(document_auth_tag="0" * 64)
        return document.model_copy(
            update={
                "document_auth_tag": _document_auth_tag(
                    document, self._integrity_key
                )
            }
        )

    def load(self) -> EvaluationHistoryDocument:
        if not self._path.exists():
            return self._empty_document()
        try:
            document = EvaluationHistoryDocument.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise EvaluationHistoryError("评测历史文件无法通过协议校验") from exc
        return _validate_document(document, self._integrity_key)

    def append(
        self,
        run: EvaluationRun,
        *,
        baseline: EvaluationRun | None = None,
        policy: GatePolicy | None = None,
        dataset_cases: tuple[DatasetCase, ...] | None = None,
    ) -> EvaluationHistoryRecord:
        document = self.load()
        summaries = {record.summary.run_id: record.summary for record in document.records}
        if run.run_id in summaries:
            raise EvaluationHistoryError(f"评测运行已存在: {run.run_id}")

        if document.records and baseline is None:
            raise EvaluationHistoryError("非首个趋势运行必须提供已入历史的 Baseline")
        if not document.records and baseline is not None:
            raise EvaluationHistoryError("首个历史记录不能声明 Baseline")

        if baseline is None:
            record = EvaluationHistoryRecord(
                summary=EvaluationRunSummary.from_run(run),
                record_auth_tag="0" * 64,
            )
        else:
            persisted = summaries.get(baseline.run_id)
            if persisted is None or persisted.run_content_hash != baseline.content_hash():
                raise EvaluationHistoryError("Baseline 必须已入历史且内容哈希一致")
            changes = comparison_changes(baseline, run)
            if changes:
                raise TrendComparabilityError(changes)
            gate = compare_runs(
                AuthenticatedEvaluationRun.issue(baseline, self._integrity_key),
                AuthenticatedEvaluationRun.issue(run, self._integrity_key),
                policy,
                dataset_cases=(
                    AuthenticatedDatasetCases.issue(
                        run.identity.dataset_name,
                        dataset_cases,
                        self._integrity_key,
                    )
                    if dataset_cases is not None
                    else None
                ),
                integrity_key=self._integrity_key,
            )
            policy_snapshot = (policy or GatePolicy()).model_dump(mode="json")
            record = EvaluationHistoryRecord(
                summary=EvaluationRunSummary.from_run(run),
                baseline_run_id=baseline.run_id,
                gate_allowed=gate.allowed,
                blocked_by_p0=gate.blocked_by_p0,
                blocking_reasons=gate.blocking_reasons,
                gate_policy=policy_snapshot,
                gate_policy_hash=sha256_text(canonical_json(policy_snapshot)),
                record_auth_tag="0" * 64,
            )
        record = record.model_copy(
            update={"record_auth_tag": _record_auth_tag(record, self._integrity_key)}
        )
        updated = document.model_copy(
            update={"records": (*document.records, record), "document_auth_tag": "0" * 64}
        )
        updated = updated.model_copy(
            update={
                "document_auth_tag": _document_auth_tag(updated, self._integrity_key)
            }
        )
        self._write(updated)
        return record

    def trend(self) -> EvaluationTrendReport:
        document = self.load()
        if not document.records:
            raise EvaluationHistoryError("没有可生成趋势的评测运行")
        fingerprints = {item.summary.comparison_fingerprint for item in document.records}
        if len(fingerprints) != 1:
            raise EvaluationHistoryError("历史包含不可比运行，拒绝生成混合趋势")
        first = document.records[0].summary
        points = tuple(_trend_point(item) for item in document.records)
        return EvaluationTrendReport(
            comparison_fingerprint=first.comparison_fingerprint,
            dataset_name=first.dataset_name,
            dataset_version=first.dataset_version,
            model_name=first.model_name,
            prompt_hash=first.prompt_hash,
            configuration_hash=first.configuration_hash,
            points=points,
            blocked_run_ids=tuple(
                point.run_id for point in points if point.gate_allowed is False
            ),
        )

    def _write(self, document: EvaluationHistoryDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(document.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self._path)


def write_evaluation_trend_report(
    report: EvaluationTrendReport, output_directory: Path
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "phase8-evaluation-trend.json"
    markdown_path = output_directory / "phase8-evaluation-trend.md"
    _atomic_write(json_path, report.model_dump_json(indent=2))
    _atomic_write(markdown_path, report.to_markdown())
    return json_path, markdown_path


def comparison_changes(
    baseline: EvaluationRun, candidate: EvaluationRun
) -> tuple[str, ...]:
    """Name controlled variables that changed; facts and outputs are never inspected."""
    before = baseline.identity
    after = candidate.identity
    fields = (
        "dataset_name", "dataset_version", "split", "runner_name", "model_name",
        "model_parameters", "prompt_hash", "configuration_hash", "environment",
    )
    return tuple(name for name in fields if getattr(before, name) != getattr(after, name))


def _trend_point(record: EvaluationHistoryRecord) -> TrendPoint:
    summary = record.summary
    return TrendPoint(
        run_id=summary.run_id,
        code_commit=summary.code_commit,
        created_at=summary.created_at,
        baseline_run_id=record.baseline_run_id,
        gate_allowed=record.gate_allowed,
        metrics={name: float(getattr(summary.metrics, name)) for name, _ in CORE_METRICS},
        deterministic_p0_count=summary.deterministic_p0_count,
    )


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
