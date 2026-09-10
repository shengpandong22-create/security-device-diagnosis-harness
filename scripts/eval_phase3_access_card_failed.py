"""Phase 3C 门禁刷卡异常评测脚本。

运行：

    uv run python scripts/eval_phase3_access_card_failed.py

跑 `samples/devices/access_card_failed_cases.json` 中的固定子案例，
输出每个案例的候选根因、引用合规情况和总体统计：

    demo-output/phase3-access-card-failed-eval.json
    demo-output/phase3-access-card-failed-eval.md

全程使用 FakeLLM 与 StaticDeviceGateway：不调用真实模型，不访问真实设备。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.bootstrap.container import (  # noqa: E402
    build_phase3_container,
)
from security_diagnosis_harness.domain.citation_policy import (  # noqa: E402
    DEVICE_FACT_EVIDENCE_TYPES,
    MIN_DEVICE_FACT_TYPES_FOR_PROBABLE,
)
from security_diagnosis_harness.domain.conclusion import ConclusionConfidence  # noqa: E402
from security_diagnosis_harness.domain.device import REDACTED_VALUE  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.domain.review import HumanReviewAction  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "demo-output"
JSON_PATH = OUTPUT_DIR / "phase3-access-card-failed-eval.json"
MD_PATH = OUTPUT_DIR / "phase3-access-card-failed-eval.md"
CASE_REPORT_DIR = OUTPUT_DIR / "phase3-cases"

REVIEWER = "eval-harness"
DESCRIPTION = "门禁刷卡失败，需要定位凭证、权限、时段、控制器或门锁根因"

# 样例数据中必须被脱敏的占位信息，用于统计敏感信息泄露。
SENSITIVE_MARKERS: tuple[str, ...] = (
    "sample-admin-pwd-not-real",
    "sample-card-frozen",
    "sample-card-no-permission",
    "sample-card-time-window",
    "sample-card-controller-offline",
    "sample-card-lock-jammed",
    "sample-person-frozen",
    "sample-person-no-permission",
    "sample-person-time-window",
    "sample-person-controller-offline",
    "sample-person-lock-jammed",
    "sample-card-no-not-real",
)


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def count_sensitive_leaks(text: str) -> int:
    """统计文本中出现的未脱敏凭证或人员标识数量。"""
    return sum(1 for marker in SENSITIVE_MARKERS if marker in text)


def check_citation_compliance(case, conclusion) -> bool:
    """判断引用是否合规。"""
    if conclusion is None or not conclusion.cited_evidence_ids:
        return False
    if conclusion.confidence not in tuple(ConclusionConfidence):
        return False

    known = {evidence.evidence_id: evidence for evidence in case.evidence}
    cited = [known.get(evidence_id) for evidence_id in conclusion.cited_evidence_ids]
    if any(item is None for item in cited):
        return False

    if conclusion.confidence is ConclusionConfidence.PROBABLE:
        fact_types = {
            item.evidence_type for item in cited if item.evidence_type in DEVICE_FACT_EVIDENCE_TYPES
        }
        if len(fact_types) < MIN_DEVICE_FACT_TYPES_FOR_PROBABLE:
            return False
    return True


def run_eval() -> dict:
    """跑完全部固定案例，返回评测结果字典。"""
    container = build_phase3_container()
    service = container.service
    cases = container.gateway.list_cases()

    results: list[dict] = []
    for ref in cases:
        expected = ref.expected_label or ""
        case = service.create_diagnosis(
            device_id=ref.device_id,
            fault_type=SecurityFaultType.ACCESS_CARD_FAILED,
            reporter=REVIEWER,
            description=DESCRIPTION,
        )
        run = service.run_diagnosis(case.diagnosis_id)
        evidence = service.list_evidence(case.diagnosis_id)

        actual = run.candidate_label.value if run.candidate_label else ""
        matched = bool(actual) and actual == expected

        review_status = None
        if run.ok:
            review = service.review_diagnosis(
                diagnosis_id=case.diagnosis_id,
                action=HumanReviewAction.CONFIRM,
                reviewer=REVIEWER,
                comment="固定案例回归：确认候选结论",
            )
            review_status = review.status.value

        report_path = CASE_REPORT_DIR / f"{ref.case_id}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = service.render_report(case.diagnosis_id)
        report_path.write_text(markdown, encoding="utf-8")

        final_case = service.get_diagnosis(case.diagnosis_id)
        conclusion = final_case.conclusion
        leaks = count_sensitive_leaks(markdown)

        results.append(
            {
                "case_id": ref.case_id,
                "device_id": ref.device_id,
                "expected_label": expected,
                "actual_label": actual,
                "matched": matched,
                "status": final_case.status.value,
                "evidence_count": len(evidence),
                "cited_evidence_ids": (
                    list(conclusion.cited_evidence_ids) if conclusion else []
                ),
                "confidence": conclusion.confidence.value if conclusion else None,
                "citation_compliant": check_citation_compliance(final_case, conclusion),
                "external_model_called": container.external_model_called,
                "sensitive_leak_count": leaks,
                "report_path": _relative(report_path),
                "review_status": review_status,
                "error": run.error,
            }
        )

    total = len(results)
    passed = sum(1 for item in results if item["matched"])
    compliant = sum(1 for item in results if item["citation_compliant"])
    leak_count = sum(item["sensitive_leak_count"] for item in results)

    summary = {
        "total": total,
        "passed": passed,
        "label_accuracy": round(passed / total, 4) if total else 0.0,
        "citation_compliance": round(compliant / total, 4) if total else 0.0,
        "external_model_called": container.external_model_called,
        "sensitive_leak_count": leak_count,
        "redaction_marker": REDACTED_VALUE,
    }
    return {"summary": summary, "cases": results}


def render_markdown(payload: dict) -> str:
    """把评测结果渲染成 Markdown。"""
    summary = payload["summary"]
    lines = [
        "# Phase 3C 门禁刷卡异常评测报告",
        "",
        "## 1. 总体统计",
        "",
        f"- total: {summary['total']}",
        f"- passed: {summary['passed']}",
        f"- label_accuracy: {summary['label_accuracy']}",
        f"- citation_compliance: {summary['citation_compliance']}",
        f"- external_model_called: {str(summary['external_model_called']).lower()}",
        f"- sensitive_leak_count: {summary['sensitive_leak_count']}",
        "",
        "## 2. 案例明细",
        "",
        "| case_id | device_id | expected_label | actual_label | matched | status "
        "| evidence_count | citation_compliant |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in payload["cases"]:
        lines.append(
            f"| {item['case_id']} | {item['device_id']} | {item['expected_label']} "
            f"| {item['actual_label']} | {'yes' if item['matched'] else 'no'} "
            f"| {item['status']} | {item['evidence_count']} "
            f"| {'yes' if item['citation_compliant'] else 'no'} |"
        )

    lines += ["", "## 3. 每个案例的引用与报告", ""]
    for item in payload["cases"]:
        lines += [
            f"### {item['case_id']}（{item['device_id']}）",
            "",
            f"- expected_label: `{item['expected_label']}`",
            f"- actual_label: `{item['actual_label']}`",
            f"- matched: {'yes' if item['matched'] else 'no'}",
            f"- status: {item['status']}",
            f"- evidence_count: {item['evidence_count']}",
            f"- cited_evidence_ids: {', '.join(item['cited_evidence_ids']) or '（无）'}",
            f"- external_model_called: {str(item['external_model_called']).lower()}",
            f"- sensitive_leak_count: {item['sensitive_leak_count']}",
            f"- report: `{item['report_path']}`",
            "",
        ]

    lines += [
        "## 4. 说明",
        "",
        "- 评测使用 FakeLLM 与本地静态样例，不调用真实模型，不访问真实设备。",
        "- confirmed 由固定案例回归中的人工 review 动作产生，不是模型或规则直接产生。",
        "- candidate_label 只是候选根因解释，不等同于已确认根因。",
        "- 门禁类证据（控制器 / 门 / 凭证 / 权限 / 事件）已纳入 CitationPolicy 设备事实类型。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    """执行评测并写出 JSON / Markdown。"""
    payload = run_eval()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    MD_PATH.write_text(render_markdown(payload), encoding="utf-8")

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
