"""Phase 8C safe evaluation history, comparable trends, and gate lineage."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.redaction import redact_mapping
from security_diagnosis_harness.evaluation.dataset import DatasetSplit
from security_diagnosis_harness.evaluation.gate import (
    CORE_METRICS,
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
        controlled = identity.controlled_variables()
        p0_count = sum(case.p0_blocked for case in run.grade.cases)
        metrics = run.grade.metrics.model_copy(update={"p0_failure_count": p0_count})
        return cls(
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
            comparison_fingerprint=sha256_text(canonical_json(controlled)),
            run_content_hash=run.content_hash(),
            metrics=metrics,
            deterministic_p0_count=p0_count,
        )


class EvaluationHistoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: EvaluationRunSummary
    baseline_run_id: str | None = None
    gate_allowed: bool | None = None
    blocked_by_p0: bool = False
    blocking_reasons: tuple[str, ...] = ()


class EvaluationHistoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0.0"
    records: tuple[EvaluationHistoryRecord, ...] = ()


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

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> EvaluationHistoryDocument:
        if not self._path.exists():
            return EvaluationHistoryDocument()
        try:
            return EvaluationHistoryDocument.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise EvaluationHistoryError("评测历史文件无法通过协议校验") from exc

    def append(
        self,
        run: EvaluationRun,
        *,
        baseline: EvaluationRun | None = None,
        policy: GatePolicy | None = None,
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
            record = EvaluationHistoryRecord(summary=EvaluationRunSummary.from_run(run))
        else:
            persisted = summaries.get(baseline.run_id)
            if persisted is None or persisted.run_content_hash != baseline.content_hash():
                raise EvaluationHistoryError("Baseline 必须已入历史且内容哈希一致")
            changes = comparison_changes(baseline, run)
            if changes:
                raise TrendComparabilityError(changes)
            gate = compare_runs(baseline, run, policy)
            record = EvaluationHistoryRecord(
                summary=EvaluationRunSummary.from_run(run),
                baseline_run_id=baseline.run_id,
                gate_allowed=gate.allowed,
                blocked_by_p0=gate.blocked_by_p0,
                blocking_reasons=gate.blocking_reasons,
            )
        updated = document.model_copy(update={"records": (*document.records, record)})
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
