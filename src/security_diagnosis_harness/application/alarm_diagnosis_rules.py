"""报警误报 / 误触发候选根因规则。

这层和摄像头、录像、门禁规则保持同一边界：

- 只读取当前诊断已经落地的 Evidence；
- 输出候选标签、证据链、排查顺序和排除项；
- 不读取样例 JSON，不访问真实设备，不调用真实模型；
- 规则只产生 candidate，不产生 confirmed，也不绕过 CitationPolicy。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.alarm import (
    BURST_ALARM_COUNT,
    HIGH_NOISE_LEVEL,
    LOW_RULE_THRESHOLD,
    SHORT_DEBOUNCE_SECONDS,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence, EvidenceType


class AlarmDiagnosisLabel(StrEnum):
    """报警误报候选根因标签。"""

    ALARM_RULE_TOO_SENSITIVE = "alarm_rule_too_sensitive"
    ENVIRONMENT_INTERFERENCE = "environment_interference"
    SENSOR_NOISE_OR_STUCK = "sensor_noise_or_stuck"
    VERIFICATION_NEGATIVE_FALSE_ALARM = "verification_negative_false_alarm"
    DUPLICATE_ALARM_BURST = "duplicate_alarm_burst"
    INSUFFICIENT_ALARM_EVIDENCE = "insufficient_alarm_evidence"


LABEL_EXPLANATIONS: dict[AlarmDiagnosisLabel, str] = {
    AlarmDiagnosisLabel.ALARM_RULE_TOO_SENSITIVE: (
        "报警规则灵敏度过高、阈值过低或防抖时间过短，导致轻微信号也触发告警"
    ),
    AlarmDiagnosisLabel.ENVIRONMENT_INTERFERENCE: (
        "雨、雾、强光、风、夜间或阴影等环境因素造成误触发"
    ),
    AlarmDiagnosisLabel.SENSOR_NOISE_OR_STUCK: (
        "传感器信号噪声过高、卡死或信号缺失，导致告警触发不稳定"
    ),
    AlarmDiagnosisLabel.VERIFICATION_NEGATIVE_FALSE_ALARM: (
        "复核未发现真实目标，当前告警更像是误报而非真实入侵或事件"
    ),
    AlarmDiagnosisLabel.DUPLICATE_ALARM_BURST: (
        "短时间重复告警或相邻设备联动异常，形成重复告警风暴"
    ),
    AlarmDiagnosisLabel.INSUFFICIENT_ALARM_EVIDENCE: (
        "缺少报警规则、信号、环境、复核或关联告警等关键事实，无法给出可靠候选"
    ),
}

TROUBLESHOOTING_ORDER: dict[AlarmDiagnosisLabel, list[str]] = {
    AlarmDiagnosisLabel.ALARM_RULE_TOO_SENSITIVE: [
        "核对报警规则的灵敏度、阈值和防抖时间",
        "对比同类设备的规则模板，确认是否明显偏敏",
        "在测试环境调低灵敏度或提高阈值后观察误报是否下降",
        "保留规则调整前后的告警数量作为复核证据",
    ],
    AlarmDiagnosisLabel.ENVIRONMENT_INTERFERENCE: [
        "查看告警发生时段的雨雾、强光、风、夜间或阴影因素",
        "复核摄像机安装角度和画面遮挡情况",
        "调整检测区域、屏蔽区域或环境补偿参数后复测",
        "在相似天气或光照条件下观察告警是否复现",
    ],
    AlarmDiagnosisLabel.SENSOR_NOISE_OR_STUCK: [
        "检查传感器信号值、噪声等级和阈值关系",
        "确认传感器是否存在抖动、卡死或信号缺失",
        "排查线路、供电和传感器硬件状态",
        "更换或校准传感器后复测告警触发稳定性",
    ],
    AlarmDiagnosisLabel.VERIFICATION_NEGATIVE_FALSE_ALARM: [
        "复核告警时间点的视频或截图，确认是否存在真实目标",
        "核对算法识别框、区域规则和现场物体移动情况",
        "将复核结果作为人工确认依据，避免自动关闭告警",
        "必要时补充更多相邻时间片段再次诊断",
    ],
    AlarmDiagnosisLabel.DUPLICATE_ALARM_BURST: [
        "统计短时间内重复告警次数和相邻设备告警数量",
        "确认是否同一事件被多规则或多设备重复触发",
        "检查去重窗口、告警合并策略和联动配置",
        "调整去重策略后观察告警风暴是否收敛",
    ],
    AlarmDiagnosisLabel.INSUFFICIENT_ALARM_EVIDENCE: [
        "补充采集报警规则、触发信号、环境、复核和关联告警事实",
        "确认只读工具是否全部执行成功",
        "补充现场描述后重新运行诊断",
    ],
}

_NOISY_SIGNAL_STATUSES: frozenset[str] = frozenset({"noisy", "stuck", "missing"})
_FALSE_ALARM_VERIFICATION_RESULTS: frozenset[str] = frozenset({"no_target_found"})
_BURST_PATTERNS: frozenset[str] = frozenset({"burst", "multi_device"})


@dataclass
class AlarmFacts:
    """从 Evidence 中提取出的报警误报事实。"""

    rule_evidence: DiagnosisEvidence | None = None
    signal_evidence: DiagnosisEvidence | None = None
    environment_evidence: DiagnosisEvidence | None = None
    verification_evidence: DiagnosisEvidence | None = None
    correlation_evidence: DiagnosisEvidence | None = None

    rule_over_sensitive: bool = False
    signal_status: str | None = None
    signal_noisy: bool = False
    environment_has_interference: bool = False
    verification_negative: bool = False
    correlation_burst: bool = False

    missing: list[str] = field(default_factory=list)


class AlarmDiagnosisRuleResult(BaseModel):
    """报警误报候选根因规则的输出。"""

    model_config = ConfigDict(extra="forbid")

    label: AlarmDiagnosisLabel
    explanation: str
    evidence_chain: list[str] = Field(default_factory=list)
    excluded_candidates: list[str] = Field(default_factory=list)
    troubleshooting_order: list[str] = Field(default_factory=list)
    matched_rule: str = ""


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_result(
    label: AlarmDiagnosisLabel,
    matched_rule: str,
    evidence_chain: list[str],
) -> AlarmDiagnosisRuleResult:
    excluded = [
        f"{other.value}：{explanation}"
        for other, explanation in LABEL_EXPLANATIONS.items()
        if other is not label
    ]
    return AlarmDiagnosisRuleResult(
        label=label,
        explanation=LABEL_EXPLANATIONS[label],
        evidence_chain=evidence_chain,
        excluded_candidates=excluded,
        troubleshooting_order=list(TROUBLESHOOTING_ORDER[label]),
        matched_rule=matched_rule,
    )


def extract_alarm_facts(case: SecurityDiagnosisCase) -> AlarmFacts:
    """从诊断 Evidence 中提取报警误报事实。"""
    facts = AlarmFacts()

    for item in case.evidence:
        if item.evidence_type is EvidenceType.ALARM_RULE:
            facts.rule_evidence = item
        elif item.evidence_type is EvidenceType.ALARM_SIGNAL:
            facts.signal_evidence = item
        elif item.evidence_type is EvidenceType.ALARM_ENVIRONMENT:
            facts.environment_evidence = item
        elif item.evidence_type is EvidenceType.ALARM_VERIFICATION:
            facts.verification_evidence = item
        elif item.evidence_type is EvidenceType.ALARM_CORRELATION:
            facts.correlation_evidence = item

    if facts.rule_evidence is None:
        facts.missing.append("alarm_rule")
    if facts.signal_evidence is None:
        facts.missing.append("alarm_signal")
    if facts.environment_evidence is None:
        facts.missing.append("alarm_environment")
    if facts.verification_evidence is None:
        facts.missing.append("alarm_verification")
    if facts.correlation_evidence is None:
        facts.missing.append("alarm_correlation")

    if facts.rule_evidence is not None:
        payload = facts.rule_evidence.payload or {}
        sensitivity = _as_str(payload.get("sensitivity"))
        threshold = _as_float(payload.get("threshold"))
        debounce_seconds = _as_int(payload.get("debounce_seconds"))
        facts.rule_over_sensitive = (
            bool(payload.get("is_over_sensitive", False))
            or sensitivity == "high"
            or (threshold is not None and threshold <= LOW_RULE_THRESHOLD)
            or (
                debounce_seconds is not None
                and debounce_seconds < SHORT_DEBOUNCE_SECONDS
            )
        )

    if facts.signal_evidence is not None:
        payload = facts.signal_evidence.payload or {}
        facts.signal_status = _as_str(payload.get("status"))
        noise_level = _as_float(payload.get("noise_level"))
        facts.signal_noisy = (
            facts.signal_status in _NOISY_SIGNAL_STATUSES
            or bool(payload.get("is_noisy", False))
            or (noise_level is not None and noise_level >= HIGH_NOISE_LEVEL)
        )

    if facts.environment_evidence is not None:
        payload = facts.environment_evidence.payload or {}
        interferences = {
            str(item)
            for item in payload.get("interference_types", [])
            if item is not None
        }
        facts.environment_has_interference = bool(
            payload.get("has_interference", False)
        ) or any(item != "none" for item in interferences)

    if facts.verification_evidence is not None:
        payload = facts.verification_evidence.payload or {}
        result = _as_str(payload.get("result"))
        indicates = _as_bool(payload.get("indicates_false_alarm"))
        facts.verification_negative = (
            result in _FALSE_ALARM_VERIFICATION_RESULTS
            or indicates is True
        )

    if facts.correlation_evidence is not None:
        payload = facts.correlation_evidence.payload or {}
        pattern = _as_str(payload.get("pattern"))
        repeated_count = _as_int(payload.get("repeated_count"))
        neighbor_alarm_count = _as_int(payload.get("neighbor_alarm_count"))
        facts.correlation_burst = (
            pattern in _BURST_PATTERNS
            or bool(payload.get("is_burst", False))
            or bool(payload.get("has_neighbor_correlation", False))
            or (repeated_count is not None and repeated_count >= BURST_ALARM_COUNT)
            or (neighbor_alarm_count is not None and neighbor_alarm_count > 0)
        )

    return facts


def infer_alarm_false_positive_label(case: SecurityDiagnosisCase) -> AlarmDiagnosisRuleResult:
    """根据诊断 Evidence 推断报警误报候选根因。

    判定顺序遵循"规则 -> 环境 -> 信号 -> 复核 -> 关联"：

    1. 规则明显过敏 -> alarm_rule_too_sensitive；
    2. 存在雨雾强光风夜间阴影等干扰 -> environment_interference；
    3. 信号噪声、卡死或缺失 -> sensor_noise_or_stuck；
    4. 复核未发现真实目标 -> verification_negative_false_alarm；
    5. 短时间重复或相邻设备关联 -> duplicate_alarm_burst；
    6. 缺少关键事实或无法区分 -> insufficient_alarm_evidence。
    """
    facts = extract_alarm_facts(case)
    evidence_chain = [
        item.evidence_id
        for item in (
            facts.rule_evidence,
            facts.signal_evidence,
            facts.environment_evidence,
            facts.verification_evidence,
            facts.correlation_evidence,
        )
        if item is not None
    ]

    if facts.rule_evidence is None or facts.signal_evidence is None:
        reason = "、".join(facts.missing) or "alarm_rule/alarm_signal"
        return _build_result(
            AlarmDiagnosisLabel.INSUFFICIENT_ALARM_EVIDENCE,
            "R0 缺少关键报警规则或触发信号事实",
            [f"缺少 {reason}，仅收集到：{evidence_chain or ['（无）']}"],
        )

    if facts.rule_over_sensitive:
        return _build_result(
            AlarmDiagnosisLabel.ALARM_RULE_TOO_SENSITIVE,
            "R1 报警规则过敏",
            evidence_chain,
        )

    if facts.environment_has_interference:
        return _build_result(
            AlarmDiagnosisLabel.ENVIRONMENT_INTERFERENCE,
            "R2 环境干扰",
            evidence_chain,
        )

    if facts.signal_noisy:
        return _build_result(
            AlarmDiagnosisLabel.SENSOR_NOISE_OR_STUCK,
            "R3 传感器信号异常",
            evidence_chain,
        )

    if facts.verification_negative:
        return _build_result(
            AlarmDiagnosisLabel.VERIFICATION_NEGATIVE_FALSE_ALARM,
            "R4 复核未发现真实目标",
            evidence_chain,
        )

    if facts.correlation_burst:
        return _build_result(
            AlarmDiagnosisLabel.DUPLICATE_ALARM_BURST,
            "R5 重复 / 关联告警风暴",
            evidence_chain,
        )

    return _build_result(
        AlarmDiagnosisLabel.INSUFFICIENT_ALARM_EVIDENCE,
        "R6 证据不足以唯一确定候选根因",
        evidence_chain,
    )
