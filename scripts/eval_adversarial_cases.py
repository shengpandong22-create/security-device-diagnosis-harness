"""Run the isolated adversarial Development suite without network or secrets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.bootstrap.container import (  # noqa: E402
    build_phase1_container,
    build_phase2_container,
    build_phase3_container,
    build_phase4_container,
)
from security_diagnosis_harness.domain.conclusion import ConclusionConfidence  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus  # noqa: E402
from security_diagnosis_harness.evaluation.adversarial import (  # noqa: E402
    AdversarialCase,
    AdversarialCaseResult,
    AdversarialFixture,
    AdversarialOutcome,
    AdversarialReport,
    OracleCheck,
    classify_outcome,
    load_adversarial_fixture,
)
from security_diagnosis_harness.evaluation.attribution import FAILURE_CATALOG  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "evaluation-fixtures/adversarial-development-1.0.0.json"
DEFAULT_JSON = ROOT / "docs/04-validation/adversarial-development-report.json"
DEFAULT_MARKDOWN = ROOT / "docs/04-validation/adversarial-development-report.md"

_BUILDERS = {
    "camera_black_screen": build_phase1_container,
    "recording_missing": build_phase2_container,
    "access_card_failed": build_phase3_container,
    "alarm_false_positive": build_phase4_container,
}
_CONFIDENCE_RANK = {
    ConclusionConfidence.POSSIBLE: 0,
    ConclusionConfidence.PROBABLE: 1,
}


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _check(name: str, passed: bool, expected: object, actual: object) -> OracleCheck:
    return OracleCheck(name=name, passed=passed, expected=expected, actual=actual)


def _attribution_codes(checks: tuple[OracleCheck, ...]) -> tuple[str, ...]:
    mapping = {
        "candidate_label": "candidate_mismatch",
        "maximum_confidence": "unsupported_claim",
        "minimum_evidence_count": "required_evidence_missing",
        "maximum_evidence_count": "unsupported_claim",
        "required_evidence_types": "required_evidence_missing",
        "no_fabricated_evidence": "citation_noncompliance",
        "controlled_degradation": "task_incomplete",
        "no_automatic_review": "automatic_confirmed",
        "non_confirmed": "automatic_confirmed",
    }
    return tuple(sorted({mapping[item.name] for item in checks if not item.passed}))


def _evaluate_case(case: AdversarialCase, directory: Path) -> AdversarialCaseResult:
    dataset_path = directory / f"{case.case_id}.json"
    dataset_path.write_text(
        json.dumps({"version": 1, "devices": [case.gateway_entry]}, ensure_ascii=False),
        encoding="utf-8",
    )
    container = _BUILDERS[case.fault_type.value](dataset_path)
    service = container.service
    diagnosis = service.create_diagnosis(
        device_id=str(case.gateway_entry["device_id"]),
        fault_type=case.fault_type,
        reporter="adversarial-eval",
        description=case.design_intent,
    )
    run = service.run_diagnosis(diagnosis.diagnosis_id)
    final_case = service.get_diagnosis(diagnosis.diagnosis_id)
    conclusion = final_case.conclusion
    actual_label = run.candidate_label.value if run.candidate_label else None
    confidence = conclusion.confidence if conclusion else None
    evidence_types = tuple(item.evidence_type for item in final_case.evidence)
    evidence_ids = {item.evidence_id for item in final_case.evidence}
    cited_ids = tuple(conclusion.cited_evidence_ids) if conclusion else ()
    controlled_degradation = bool(
        run.degraded
        or not run.ok
        or (actual_label is not None and actual_label.startswith("insufficient_"))
    )
    oracle = case.oracle
    confidence_ok = confidence is None or (
        _CONFIDENCE_RANK[confidence] <= _CONFIDENCE_RANK[oracle.maximum_confidence]
    )
    required = set(oracle.required_evidence_types)
    checks = (
        _check(
            "candidate_label",
            actual_label == oracle.expected_candidate_label,
            oracle.expected_candidate_label,
            actual_label,
        ),
        _check(
            "maximum_confidence",
            confidence_ok,
            oracle.maximum_confidence.value,
            confidence.value if confidence else None,
        ),
        _check(
            "minimum_evidence_count",
            len(final_case.evidence) >= oracle.minimum_evidence_count,
            oracle.minimum_evidence_count,
            len(final_case.evidence),
        ),
        _check(
            "maximum_evidence_count",
            oracle.maximum_evidence_count is None
            or len(final_case.evidence) <= oracle.maximum_evidence_count,
            oracle.maximum_evidence_count,
            len(final_case.evidence),
        ),
        _check(
            "required_evidence_types",
            required.issubset(set(evidence_types)),
            sorted(item.value for item in required),
            sorted({item.value for item in evidence_types}),
        ),
        _check(
            "no_fabricated_evidence",
            not oracle.expected_no_fabricated_evidence or set(cited_ids).issubset(evidence_ids),
            True,
            set(cited_ids).issubset(evidence_ids),
        ),
        _check(
            "controlled_degradation",
            controlled_degradation == oracle.expected_controlled_degradation,
            oracle.expected_controlled_degradation,
            controlled_degradation,
        ),
        _check(
            "no_automatic_review",
            not oracle.expected_no_automatic_review or not final_case.reviews,
            True,
            not final_case.reviews,
        ),
        _check(
            "non_confirmed",
            not oracle.expected_non_confirmed
            or final_case.status is not SecurityDiagnosisStatus.CONFIRMED,
            True,
            final_case.status is not SecurityDiagnosisStatus.CONFIRMED,
        ),
    )
    oracle_passed = all(item.passed for item in checks)
    attribution_codes = _attribution_codes(checks)
    outcome = classify_outcome(
        oracle_passed=oracle_passed,
        expected_degradation=oracle.expected_controlled_degradation,
    )
    return AdversarialCaseResult(
        case_id=case.case_id,
        fault_type=case.fault_type,
        trap_type=case.trap_type,
        expected_candidate_label=oracle.expected_candidate_label,
        actual_candidate_label=actual_label,
        actual_confidence=confidence,
        actual_status=final_case.status,
        evidence_count=len(final_case.evidence),
        evidence_types=evidence_types,
        cited_evidence_ids=cited_ids,
        controlled_degradation=controlled_degradation,
        error=run.error,
        checks=checks,
        outcome=outcome,
        attribution_codes=attribution_codes,
    )


def evaluate(
    fixture_path: Path = DEFAULT_FIXTURE,
    *,
    code_commit: str | None = None,
    run_at: datetime | None = None,
) -> AdversarialReport:
    fixture = load_adversarial_fixture(fixture_path)
    with TemporaryDirectory(prefix="adversarial-eval-") as temporary:
        results = tuple(_evaluate_case(case, Path(temporary)) for case in fixture.cases)
    counts = Counter(item.outcome for item in results)
    return AdversarialReport(
        report_kind="adversarial_eval",
        fixture_version=fixture.fixture_version,
        dataset_version=fixture.dataset_version,
        fixture_hash=fixture.content_hash(),
        code_commit=code_commit or _git_head(),
        run_at=run_at or datetime.now(UTC),
        runner_name="formal_application_static_fake",
        external_model_called=False,
        real_device_accessed=False,
        network_accessed=False,
        rules_modified_for_results=False,
        evaluation_miss_measurement=(
            "not_automatically_measurable_without_independent_basis"
        ),
        cases=results,
        outcome_counts={outcome: counts.get(outcome, 0) for outcome in AdversarialOutcome},
    )


def render_markdown(report: AdversarialReport, fixture: AdversarialFixture) -> str:
    lines = [
        "# Adversarial Development 对抗评测报告",
        "",
        "## 目的与非目标",
        "",
        "本报告验证冲突事实、工具失败、关键事实缺失和弱证据条件下的确定性安全行为，",
        "并验证评测能否如实暴露偏差。它不评估真实模型、真实设备或生产准确率，",
        "也不与 fixed_case、simulator_e2e、real_model 基线混算。",
        "",
        f"- report_kind: `{report.report_kind}`",
        f"- fixture_version: `{report.fixture_version}`",
        f"- fixture_hash: `{report.fixture_hash}`",
        f"- code_commit: `{report.code_commit}`",
        f"- run_at: `{report.run_at.isoformat()}`",
        f"- semantic_hash: `{report.semantic_hash()}`",
        "- external_model_called: `false`",
        "- real_device_accessed: `false`",
        "- network_accessed: `false`",
        "- rules_modified_for_results: `false`",
        "- evaluation_miss_measurement: "
        "`not_automatically_measurable_without_independent_basis`",
        "- revision 关系：`code_commit` 是本次执行所用实现；报告文件在其后的独立 "
        "report commit 中提交，因此报告提交不能自指。",
        "",
        "## 案例与预注册安全 Oracle",
        "",
        "| Case | Fault | Trap | Expected candidate | Max confidence | Evidence min/max "
        "| Degradation | Non-confirmed | No automatic review | Fact source | Intent |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    by_id = {case.case_id: case for case in fixture.cases}
    for case in fixture.cases:
        lines.append(
            f"| `{case.case_id}` | `{case.fault_type.value}` | `{case.trap_type.value}` | "
            f"`{case.oracle.expected_candidate_label}` | "
            f"`{case.oracle.maximum_confidence.value}` | "
            f"`{case.oracle.minimum_evidence_count}/"
            f"{case.oracle.maximum_evidence_count}` | "
            f"`{str(case.oracle.expected_controlled_degradation).lower()}` | "
            f"`{str(case.oracle.expected_non_confirmed).lower()}` | "
            f"`{str(case.oracle.expected_no_automatic_review).lower()}` | "
            f"{case.fact_source} | {case.design_intent} |"
        )
    lines.extend(
        [
            "",
            "## 逐案期望与实际",
            "",
            "| Case | Expected | Actual | Confidence | Evidence | Degraded | Outcome "
            "| Failed checks |",
            "|---|---|---|---|---:|---|---|---|",
        ]
    )
    for result in report.cases:
        failed = ", ".join(item.name for item in result.checks if not item.passed) or "无"
        lines.append(
            f"| `{result.case_id}` | `{result.expected_candidate_label}` | "
            f"`{result.actual_candidate_label}` | "
            f"`{result.actual_confidence.value if result.actual_confidence else None}` | "
            f"{result.evidence_count} | `{str(result.controlled_degradation).lower()}` | "
            f"`{result.outcome.value}` | {failed} |"
        )
    lines.extend(["", "## 可自动测量的结果计数", ""])
    for outcome in AdversarialOutcome:
        lines.append(f"- `{outcome.value}`: {report.outcome_counts[outcome]}")
    errors = [item for item in report.cases if not item.oracle_passed]
    lines.extend(
        [
            "",
            "## 失败归因与结论",
            "",
            f"- Oracle 检出的违规数：{len(errors)}。",
            "- 评测漏报：本轮不可自动测量。当前没有独立、预注册的 meta-oracle、"
            "人工裁决或外部判定依据，因此不报告漏报为 0。",
        ]
    )
    for item in errors:
        details = ", ".join(
            f"{code}:{FAILURE_CATALOG[code].stage.value}/{FAILURE_CATALOG[code].owner.value}"
            for code in item.attribution_codes
        )
        lines.append(f"- `{item.case_id}`：{details or '无稳定归因'}。")
    if not errors:
        lines.append(
            "- 本轮未发现系统错误；这只说明预注册的这些对抗条件通过，不证明系统不存在其他盲区。"
        )
    lines.extend(
        [
            "",
            "## 能证明与不能证明",
            "",
            "- 能证明：这些固定 Development 对抗案例会经过正式应用服务、ToolLoopRunner、",
            "  Tool Registry、CitationPolicy 和 StaticDeviceGateway，并由确定性 Oracle 检查。",
            "- 不能证明：真实摄像头、生产平台、真实模型、多厂商协议或未注册攻击面的准确率。",
            "- 没有为了结果修改四个诊断规则；没有根据输出反复改案例或调整期望。",
            "- 本报告是独立 `adversarial_eval`，不得与其他报告合并宣称总体准确率。",
            "",
        ]
    )
    del by_id
    return "\n".join(lines)


def write_report(
    report: AdversarialReport,
    fixture: AdversarialFixture,
    json_path: Path = DEFAULT_JSON,
    markdown_path: Path = DEFAULT_MARKDOWN,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report, fixture), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    fixture = load_adversarial_fixture(args.fixture)
    report = evaluate(args.fixture)
    write_report(report, fixture, args.json_output, args.markdown_output)
    print(
        json.dumps(
            {
                "report_kind": report.report_kind,
                "total": len(report.cases),
                "fixture_hash": report.fixture_hash,
                "semantic_hash": report.semantic_hash(),
                "outcome_counts": {
                    key.value: value for key, value in report.outcome_counts.items()
                },
                "external_model_called": False,
                "real_device_accessed": False,
                "network_accessed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if report.outcome_counts[AdversarialOutcome.ORACLE_DETECTED_VIOLATION] else 0


if __name__ == "__main__":
    raise SystemExit(main())
