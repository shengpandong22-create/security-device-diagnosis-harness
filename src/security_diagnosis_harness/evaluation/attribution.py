"""Phase 8B deterministic failure attribution and remediation catalog."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.evaluation.gate import EvaluationRun, RunIdentity
from security_diagnosis_harness.evaluation.grader import FindingLevel, GraderFinding


class FailureAttributionError(ValueError):
    """A finding cannot be safely attributed by the governed catalog."""


class FailureStage(StrEnum):
    DATASET = "dataset"
    PERCEPTION = "perception"
    TOOL = "tool"
    EVIDENCE = "evidence"
    CONCLUSION = "conclusion"
    MODEL = "model"
    BUDGET = "budget"
    INFRASTRUCTURE = "infrastructure"


class ResponsibilityOwner(StrEnum):
    DATA_GOVERNANCE = "data_governance"
    DEVICE_INTEGRATION = "device_integration"
    AGENT_RUNTIME = "agent_runtime"
    EVIDENCE_GOVERNANCE = "evidence_governance"
    DIAGNOSIS_POLICY = "diagnosis_policy"
    MODEL_PROMPT = "model_prompt"
    SECURITY_GOVERNANCE = "security_governance"
    PLATFORM_OPERATIONS = "platform_operations"


class FailureCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    stage: FailureStage
    owner: ResponsibilityOwner
    suggested_action: str = Field(min_length=1)


_CATALOG_ITEMS = (
    ("automatic_confirmed", FailureStage.CONCLUSION, ResponsibilityOwner.DIAGNOSIS_POLICY,
     "核验人工确认闸门，并增加非人工 confirmed 的阻断回归。"),
    ("sensitive_data_leak", FailureStage.INFRASTRUCTURE, ResponsibilityOwner.SECURITY_GOVERNANCE,
     "检查输出边界的统一脱敏与泄漏扫描，修复后重放受控案例。"),
    ("unauthorized_tool", FailureStage.TOOL, ResponsibilityOwner.AGENT_RUNTIME,
     "核验工具白名单和权限校验是否在执行前生效。"),
    ("cross_fault_execution", FailureStage.TOOL, ResponsibilityOwner.AGENT_RUNTIME,
     "核验故障域能力闸门，并增加跨域调用拒绝用例。"),
    ("budget_exceeded", FailureStage.BUDGET, ResponsibilityOwner.AGENT_RUNTIME,
     "检查轮次与工具调用预算，在不放宽上限前提下定位冗余步骤。"),
    ("timeout_exceeded", FailureStage.BUDGET, ResponsibilityOwner.PLATFORM_OPERATIONS,
     "检查耗时分布和超时边界，区分外部依赖与工作流等待。"),
    ("model_budget_exceeded", FailureStage.BUDGET, ResponsibilityOwner.MODEL_PROMPT,
     "核验模型调用计数和终止条件，减少无新增证据的重复推理。"),
    ("invalid_tool_arguments", FailureStage.TOOL, ResponsibilityOwner.MODEL_PROMPT,
     "检查工具参数契约与结构化输出提示，并增加非法参数回归。"),
    ("repeated_failed_tool_call", FailureStage.TOOL, ResponsibilityOwner.AGENT_RUNTIME,
     "检查失败调用去重与停止策略，禁止相同签名无变化重试。"),
    ("required_evidence_missing", FailureStage.EVIDENCE, ResponsibilityOwner.EVIDENCE_GOVERNANCE,
     "核验必需 Evidence 目录及采集链路，不得用推测补齐缺失事实。"),
    ("citation_noncompliance", FailureStage.EVIDENCE, ResponsibilityOwner.EVIDENCE_GOVERNANCE,
     "检查引用归属、存在性与最小引用规则，并补充反例测试。"),
    ("unsupported_claim", FailureStage.CONCLUSION, ResponsibilityOwner.DIAGNOSIS_POLICY,
     "逐项核验结论声明与 Evidence 引用，删除或降级无证据声明。"),
    ("candidate_mismatch", FailureStage.MODEL, ResponsibilityOwner.MODEL_PROMPT,
     "对照候选标签目录检查推理输入与判定边界，提交人工争议复核。"),
    ("task_incomplete", FailureStage.PERCEPTION, ResponsibilityOwner.AGENT_RUNTIME,
     "检查输入完整性、受控降级与工作流终止原因，避免猜测根因。"),
)

FAILURE_CATALOG: dict[str, FailureCatalogEntry] = {
    code: FailureCatalogEntry(code=code, stage=stage, owner=owner, suggested_action=action)
    for code, stage, owner, action in _CATALOG_ITEMS
}


class FailureAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    finding_code: str
    level: FindingLevel
    stage: FailureStage
    owner: ResponsibilityOwner
    first_seen_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    first_seen_dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    suggested_action: str

    @property
    def p0_blocking(self) -> bool:
        return self.level is FindingLevel.P0


class FailureAttributionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    identity: RunIdentity
    attributions: tuple[FailureAttribution, ...]
    affected_cases: tuple[str, ...]
    stage_counts: dict[FailureStage, int]
    owner_counts: dict[ResponsibilityOwner, int]
    p0_count: int = Field(ge=0)

    def to_markdown(self) -> str:
        lines = [
            "# Phase 8B 失败归因报告", "",
            f"- Run: `{self.run_id}`", f"- Commit: `{self.identity.code_commit}`",
            f"- Dataset: `{self.identity.dataset_name}@{self.identity.dataset_version}`",
            f"- 受影响案例: `{len(self.affected_cases)}`", f"- P0 阻塞项: `{self.p0_count}`",
            "", "## 失败明细", "",
            "| Case | Finding | Level | Stage | Owner | First seen | 建议 |",
            "|---|---|---|---|---|---|---|",
        ]
        for item in self.attributions:
            lines.append(
                f"| `{item.case_id}` | `{item.finding_code}` | `{item.level.value}` | "
                f"`{item.stage.value}` | `{item.owner.value}` | "
                f"`{item.first_seen_commit}` | {item.suggested_action} |"
            )
        return "\n".join(lines) + "\n"


def catalog_entry(finding: GraderFinding) -> FailureCatalogEntry:
    """Resolve only governed finding codes; never infer ownership from free text."""
    try:
        return FAILURE_CATALOG[finding.code]
    except KeyError as exc:
        raise FailureAttributionError(f"未治理的 Finding code: {finding.code}") from exc


def attribute_evaluation_run(
    run: EvaluationRun,
    previous_reports: Sequence[FailureAttributionReport] = (),
) -> FailureAttributionReport:
    """Attribute deterministic grader findings without accepting severity overrides."""
    first_seen: dict[tuple[str, str], FailureAttribution] = {}
    for report in previous_reports:
        for item in report.attributions:
            first_seen.setdefault((item.case_id, item.finding_code), item)
    items: list[FailureAttribution] = []
    for case in run.grade.cases:
        for finding in case.findings:
            entry = catalog_entry(finding)
            prior = first_seen.get((case.case_id, finding.code))
            items.append(
                FailureAttribution(
                    case_id=case.case_id,
                    finding_code=finding.code,
                    level=finding.level,
                    stage=entry.stage,
                    owner=entry.owner,
                    first_seen_commit=(
                        prior.first_seen_commit if prior else run.identity.code_commit
                    ),
                    first_seen_dataset_version=(
                        prior.first_seen_dataset_version
                        if prior else run.identity.dataset_version
                    ),
                    suggested_action=entry.suggested_action,
                )
            )
    ordered = tuple(sorted(items, key=lambda item: (item.case_id, item.finding_code)))
    return FailureAttributionReport(
        run_id=run.run_id,
        identity=run.identity,
        attributions=ordered,
        affected_cases=tuple(sorted({item.case_id for item in ordered})),
        stage_counts=_counts(item.stage for item in ordered),
        owner_counts=_counts(item.owner for item in ordered),
        p0_count=sum(item.p0_blocking for item in ordered),
    )


def write_failure_attribution_report(
    report: FailureAttributionReport, output_directory: Path
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "phase8-failure-attribution.json"
    markdown_path = output_directory / "phase8-failure-attribution.md"
    _atomic_write(json_path, report.model_dump_json(indent=2))
    _atomic_write(markdown_path, report.to_markdown())
    return json_path, markdown_path


def _counts(items: Iterable[object]) -> dict[object, int]:
    result: dict[object, int] = {}
    for item in items:
        result[item] = result.get(item, 0) + 1
    return result


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
