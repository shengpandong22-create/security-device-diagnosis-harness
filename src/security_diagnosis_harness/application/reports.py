"""Markdown 诊断报告渲染。

报告只展示已脱敏内容：payload 输出前会再做一次凭证字段脱敏，
避免任何未脱敏的密码/Token/secret 出现在报告里。
"""

from __future__ import annotations

import json
from typing import Any

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.device import REDACTED_VALUE, is_sensitive_key
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus

REPORT_TITLE = "# 安防设备诊断报告"

STATUS_LABELS: dict[SecurityDiagnosisStatus, str] = {
    SecurityDiagnosisStatus.CREATED: "已创建，等待运行",
    SecurityDiagnosisStatus.INVESTIGATING: "诊断中",
    SecurityDiagnosisStatus.WAITING_FOR_INPUT: "等待补充信息",
    SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION: "等待人工确认",
    SecurityDiagnosisStatus.CONFIRMED: "已由人工确认",
    SecurityDiagnosisStatus.REJECTED: "已被人工驳回",
    SecurityDiagnosisStatus.INCONCLUSIVE: "证据不足，无法定论",
}


def redact_payload(payload: Any) -> Any:
    """递归脱敏：命中凭证类键名的值统一替换为占位符。"""
    if isinstance(payload, dict):
        return {
            key: REDACTED_VALUE
            if is_sensitive_key(str(key))
            else redact_payload(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


def _dump(payload: Any) -> str:
    return json.dumps(redact_payload(payload), ensure_ascii=False, sort_keys=True, default=str)


def render_markdown_report(case: SecurityDiagnosisCase) -> str:
    """把一条诊断渲染成可读 Markdown。"""
    lines: list[str] = [
        REPORT_TITLE,
        "",
        "## 1. 基本信息",
        "",
        f"- diagnosis_id: `{case.diagnosis_id}`",
        f"- device_id: `{case.device_id}`",
        f"- fault_type: `{case.fault_type.value}`",
        f"- status: `{case.status.value}`（{STATUS_LABELS.get(case.status, case.status.value)}）",
        f"- reporter: {case.reporter}",
        f"- description: {case.description or '（未提供）'}",
        f"- created_at: {case.created_at.isoformat()}",
        f"- updated_at: {case.updated_at.isoformat()}",
        "",
        "## 2. 结论",
    ]

    if case.conclusion is None:
        lines += ["", "本次诊断暂无候选结论。", ""]
    else:
        conclusion = case.conclusion
        lines += [
            "",
            f"- conclusion_id: `{conclusion.conclusion_id}`",
            f"- summary: {conclusion.summary}",
            f"- confidence: `{conclusion.confidence.value}`",
            f"- root_cause: {conclusion.root_cause or '（未给出）'}",
            f"- created_by: {conclusion.created_by}",
            "- cited_evidence_ids:",
        ]
        if conclusion.cited_evidence_ids:
            lines += [f"  - `{evidence_id}`" for evidence_id in conclusion.cited_evidence_ids]
        else:
            lines += ["  - （无）"]
        lines += ["", "### 建议动作"]
        if conclusion.next_steps:
            lines += [f"{index}. {step}" for index, step in enumerate(conclusion.next_steps, 1)]
        else:
            lines += ["（无）"]
        lines += [""]

    lines += [
        "## 3. 证据清单",
        "",
        f"共 {len(case.evidence)} 条 Evidence。",
        "",
    ]
    if case.evidence:
        lines += [
            "| # | evidence_id | type | source | reliability | redacted | summary |",
            "|---|---|---|---|---|---|---|",
        ]
        for index, evidence in enumerate(case.evidence, 1):
            lines.append(
                f"| {index} | `{evidence.evidence_id}` | {evidence.evidence_type.value} "
                f"| {evidence.source.value} | {evidence.reliability.value} "
                f"| {'yes' if evidence.redacted else 'no'} | {evidence.summary} |"
            )
        lines += ["", "### 证据详情"]
        for index, evidence in enumerate(case.evidence, 1):
            lines += [
                "",
                f"{index}. [{evidence.evidence_type.value}] {evidence.summary}",
                f"   - evidence_id: `{evidence.evidence_id}`",
                f"   - source: {evidence.source.value}",
                f"   - reliability: {evidence.reliability.value}",
                f"   - redacted: {'yes' if evidence.redacted else 'no'}",
                f"   - content_hash: `{evidence.content_hash}`",
                f"   - payload: `{_dump(evidence.payload)}`",
            ]
    else:
        lines += ["（无 Evidence）"]
    lines += [""]

    lines += ["## 4. 人工审核记录", ""]
    if case.reviews:
        lines += [
            "| # | action | reviewer | reviewed_at | comment |",
            "|---|---|---|---|---|",
        ]
        for index, review in enumerate(case.reviews, 1):
            lines.append(
                f"| {index} | {review.action.value} | {review.reviewer} "
                f"| {review.reviewed_at.isoformat()} | {review.comment or '（无）'} |"
            )
        latest = case.reviews[-1]
        lines += [
            "",
            f"最新人工动作：`{latest.action.value}`（{latest.reviewer}）。",
        ]
    else:
        lines += ["（暂无人工审核）"]
    lines += [""]

    lines += [
        "## 5. 说明",
        "",
        "- 本报告由确定性代码生成，结论为模型候选结论，需人工确认后生效。",
        "- confirmed 只能由人工 confirm 产生，模型不能直接产生 confirmed。",
        "- 报告中的配置内容已脱敏，凭证字段统一显示为 `***REDACTED***`。",
        "",
    ]
    return "\n".join(lines)
