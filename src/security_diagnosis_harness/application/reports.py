"""Markdown 诊断报告渲染。

报告只展示已脱敏内容：payload 输出前会再做一次凭证字段脱敏，
避免任何未脱敏的密码/Token/secret 出现在报告里。
"""

from __future__ import annotations

import json
from typing import Any

from security_diagnosis_harness.application.camera_diagnosis_rules import (
    CameraDiagnosisRuleResult,
    infer_camera_black_screen_label,
)
from security_diagnosis_harness.application.recording_diagnosis_rules import (
    RecordingDiagnosisLabel,
    RecordingDiagnosisRuleResult,
    infer_recording_missing_label,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.device import REDACTED_VALUE, is_sensitive_key
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType

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


def render_markdown_report(
    case: SecurityDiagnosisCase,
    insight: CameraDiagnosisRuleResult | RecordingDiagnosisRuleResult | None = None,
) -> str:
    """把一条诊断渲染成可读 Markdown。

    `insight` 为空时，会根据故障类型基于 Evidence 重新推导候选标签
    （摄像头黑屏或录像缺失），因为规则是纯函数，报告可以离线复算。
    """
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

    if insight is None and case.fault_type is SecurityFaultType.CAMERA_BLACK_SCREEN:
        insight = infer_camera_black_screen_label(case.evidence)
    if insight is None and case.fault_type is SecurityFaultType.RECORDING_MISSING:
        insight = infer_recording_missing_label(case)

    if insight is not None:
        if isinstance(insight.label, RecordingDiagnosisLabel):
            lines += _render_recording_candidate_section(case, insight)
        else:
            lines += [
                "## 3. 候选根因与证据链",
                "",
                f"- candidate_label: `{insight.label.value}`",
                f"- 说明: {insight.explanation}",
                f"- 命中规则: {insight.matched_rule or '（未匹配）'}",
                f"- 设备事实类别数: {insight.device_fact_type_count}",
                "",
                "### 证据链解释",
            ]
            if insight.evidence_chain:
                lines += [
                    f"{index}. {item}" for index, item in enumerate(insight.evidence_chain, 1)
                ]
            else:
                lines += ["（无可解释的事实链）"]
            lines += ["", "### 建议排查顺序"]
            if insight.troubleshooting_order:
                ordered = enumerate(insight.troubleshooting_order, 1)
                lines += [f"{index}. {step}" for index, step in ordered]
            else:
                lines += ["（无）"]
            lines += ["", "### 为什么不是其他候选原因"]
            if insight.excluded_candidates:
                lines += [f"- {item}" for item in insight.excluded_candidates]
            else:
                lines += ["（无）"]
            lines += [""]

    lines += [
        "## 4. 证据清单",
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

    lines += ["## 5. 人工审核记录", ""]
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
        "## 6. 说明",
        "",
        "- 本报告由确定性代码生成，结论为模型候选结论，需人工确认后生效。",
        "- confirmed 只能由人工 confirm 产生，模型或规则都不能直接产生 confirmed。",
        "- candidate_label 是基于设备事实的候选解释，不等同于已确认根因。",
        "- 报告中的配置内容已脱敏，凭证字段统一显示为 `***REDACTED***`。",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ 录像诊断小节
def _first_evidence(case: SecurityDiagnosisCase, evidence_type: EvidenceType) -> Any | None:
    for item in case.evidence:
        if item.evidence_type is evidence_type:
            return item
    return None


def _render_recording_plan_summary(case: SecurityDiagnosisCase) -> list[str]:
    evidence = _first_evidence(case, EvidenceType.RECORDING_PLAN)
    if evidence is None:
        return ["（无录像计划证据）"]
    payload = evidence.payload or {}
    status = payload.get("status", "-")
    mode = payload.get("mode", "-")
    time_ranges = payload.get("time_ranges") or []
    retention = payload.get("retention_days", "-")
    gap_hint = "（计划启用但无有效时间段）" if not time_ranges else ""
    return [
        f"- evidence_id: `{evidence.evidence_id}`",
        f"- 状态: {status}，模式: {mode}，保留天数: {retention}{gap_hint}",
        f"- 计划时间段数: {len(time_ranges)}",
        f"- payload: `{_dump(payload)}`",
    ]


def _render_storage_summary(case: SecurityDiagnosisCase) -> list[str]:
    evidence = _first_evidence(case, EvidenceType.STORAGE_STATUS)
    if evidence is None:
        return ["（无存储状态证据）"]
    payload = evidence.payload or {}
    status = payload.get("status", "-")
    total = payload.get("total_gb", "-")
    free = payload.get("free_gb", "-")
    used = payload.get("used_percent", "-")
    last_error = payload.get("last_error") or "-"
    return [
        f"- evidence_id: `{evidence.evidence_id}`",
        f"- 状态: {status}，总容量: {total}GB，剩余: {free}GB，使用率: {used}%",
        f"- 最近错误: {last_error}",
        f"- payload: `{_dump(payload)}`",
    ]


def _render_playback_summary(case: SecurityDiagnosisCase) -> list[str]:
    evidence = _first_evidence(case, EvidenceType.PLAYBACK_CHECK)
    if evidence is None:
        return ["（无回放检查证据）"]
    payload = evidence.payload or {}
    status = payload.get("status", "-")
    file_count = payload.get("file_count", "-")
    playable = payload.get("playable", "-")
    failure = payload.get("failure_reason") or "-"
    start = payload.get("start_at", "-")
    end = payload.get("end_at", "-")
    return [
        f"- evidence_id: `{evidence.evidence_id}`",
        f"- 状态: {status}，文件数: {file_count}，可回放: {playable}",
        f"- 查询窗口: {start} ~ {end}",
        f"- 失败原因: {failure}",
        f"- payload: `{_dump(payload)}`",
    ]


def _render_recording_candidate_section(
    case: SecurityDiagnosisCase,
    insight: RecordingDiagnosisRuleResult,
) -> list[str]:
    """渲染录像缺失候选根因、证据链、排查顺序、排除项与录像事实摘要。"""
    lines: list[str] = [
        "## 3. 录像诊断（候选）",
        "",
        f"- candidate_label: `{insight.label.value}`",
        f"- 说明: {insight.explanation}",
        f"- 命中规则: {insight.matched_rule or '（未匹配）'}",
        "",
        "### 证据链解释",
    ]
    if insight.evidence_chain:
        lines += [f"{index}. {item}" for index, item in enumerate(insight.evidence_chain, 1)]
    else:
        lines += ["（无可解释的事实链）"]
    lines += ["", "### 建议排查顺序"]
    if insight.troubleshooting_order:
        lines += [f"{index}. {step}" for index, step in enumerate(insight.troubleshooting_order, 1)]
    else:
        lines += ["（无）"]
    lines += ["", "### 为什么不是其他候选原因"]
    if insight.excluded_candidates:
        lines += [f"- {item}" for item in insight.excluded_candidates]
    else:
        lines += ["（无）"]
    lines += ["", "### 录像计划摘要"]
    lines += _render_recording_plan_summary(case)
    lines += ["", "### 存储状态摘要"]
    lines += _render_storage_summary(case)
    lines += ["", "### 回放检查摘要"]
    lines += _render_playback_summary(case)
    lines += [""]
    return lines
