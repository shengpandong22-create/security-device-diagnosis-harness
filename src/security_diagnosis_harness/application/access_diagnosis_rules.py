"""门禁刷卡异常候选根因规则。

这是一层确定性辅助判断，作用和 Phase 1/2 的摄像头、录像规则一致：

- 输入只来自当前诊断已经落地的 Evidence；
- 输出是候选标签、证据链、排查顺序和排除项；
- 不读取样例 JSON，不访问真实设备，不调用模型；
- 永远不产生 confirmed，也不绕过 CitationPolicy。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence, EvidenceType


class AccessDiagnosisLabel(StrEnum):
    """门禁刷卡异常候选根因标签。"""

    CREDENTIAL_INVALID_OR_FROZEN = "credential_invalid_or_frozen"
    PERMISSION_NOT_GRANTED = "permission_not_granted"
    ACCESS_TIME_WINDOW_DENIED = "access_time_window_denied"
    CONTROLLER_OFFLINE_OR_NO_RESPONSE = "controller_offline_or_no_response"
    DOOR_LOCK_OR_SENSOR_ISSUE = "door_lock_or_sensor_issue"
    INSUFFICIENT_ACCESS_EVIDENCE = "insufficient_access_evidence"


LABEL_EXPLANATIONS: dict[AccessDiagnosisLabel, str] = {
    AccessDiagnosisLabel.CREDENTIAL_INVALID_OR_FROZEN: (
        "凭证被冻结、过期、挂失或状态无效，门禁系统拒绝通行"
    ),
    AccessDiagnosisLabel.PERMISSION_NOT_GRANTED: "人员或凭证没有目标门的通行权限",
    AccessDiagnosisLabel.ACCESS_TIME_WINDOW_DENIED: (
        "人员或凭证有门权限，但刷卡时间不在授权时段内"
    ),
    AccessDiagnosisLabel.CONTROLLER_OFFLINE_OR_NO_RESPONSE: (
        "门禁控制器离线、健康异常或通行请求超时，导致刷卡无响应"
    ),
    AccessDiagnosisLabel.DOOR_LOCK_OR_SENSOR_ISSUE: (
        "门锁、门磁或门状态异常，导致授权通过后仍无法正常开门"
    ),
    AccessDiagnosisLabel.INSUFFICIENT_ACCESS_EVIDENCE: (
        "缺少控制器、门、凭证、权限或刷卡事件等关键事实，无法给出可靠候选"
    ),
}

TROUBLESHOOTING_ORDER: dict[AccessDiagnosisLabel, list[str]] = {
    AccessDiagnosisLabel.CREDENTIAL_INVALID_OR_FROZEN: [
        "核对凭证状态是否被冻结、过期、挂失或未激活",
        "确认人员绑定的凭证是否为当前刷卡凭证",
        "由管理员恢复或重新下发凭证后复测",
        "复测后再次查看刷卡事件拒绝原因",
    ],
    AccessDiagnosisLabel.PERMISSION_NOT_GRANTED: [
        "核对人员或凭证是否被授予目标门权限",
        "确认权限策略是否同步到对应门禁控制器",
        "补齐权限后等待同步完成并复测刷卡",
        "复测后确认拒绝原因是否消失",
    ],
    AccessDiagnosisLabel.ACCESS_TIME_WINDOW_DENIED: [
        "核对刷卡发生时间与授权时段是否匹配",
        "确认授权时段的星期、跨天和有效期配置",
        "按业务要求调整授权时段后同步到控制器",
        "在合法时段内复测刷卡",
    ],
    AccessDiagnosisLabel.CONTROLLER_OFFLINE_OR_NO_RESPONSE: [
        "确认门禁控制器供电、网络与心跳状态",
        "检查控制器最近错误与平台连接状态",
        "恢复控制器在线后等待权限和事件同步",
        "复测刷卡并确认是否仍然超时",
    ],
    AccessDiagnosisLabel.DOOR_LOCK_OR_SENSOR_ISSUE: [
        "检查门锁供电、锁体机械状态与门磁反馈",
        "核对门状态是否常开、强开或长时间未关",
        "确认控制器到门锁的输出线路是否正常",
        "排除硬件问题后再次刷卡验证",
    ],
    AccessDiagnosisLabel.INSUFFICIENT_ACCESS_EVIDENCE: [
        "补充采集控制器、门、凭证、权限策略和刷卡事件事实",
        "确认只读工具是否全部执行成功",
        "补充现场描述后重新运行诊断",
    ],
}

_CREDENTIAL_ABNORMAL_STATUSES: frozenset[str] = frozenset(
    {"frozen", "expired", "lost", "unknown"}
)
_CREDENTIAL_DENY_REASONS: frozenset[str] = frozenset(
    {"frozen_credential", "expired_credential"}
)
_CONTROLLER_ABNORMAL_STATUSES: frozenset[str] = frozenset({"offline"})
_CONTROLLER_ABNORMAL_HEALTH: frozenset[str] = frozenset({"error"})
_CONTROLLER_DENY_REASONS: frozenset[str] = frozenset(
    {"controller_offline", "controller_timeout"}
)
_DOOR_ABNORMAL_STATUSES: frozenset[str] = frozenset({"forced_open", "held_open"})
_DOOR_LOCK_ABNORMAL_STATUSES: frozenset[str] = frozenset({"jammed"})
_DOOR_DENY_REASONS: frozenset[str] = frozenset({"door_lock_error"})
_TIME_WINDOW_DENY_REASONS: frozenset[str] = frozenset({"time_window_denied"})
_PERMISSION_DENY_REASONS: frozenset[str] = frozenset({"permission_denied"})


@dataclass
class AccessFacts:
    """从 Evidence 中提取出的门禁事实。"""

    controller_evidence: DiagnosisEvidence | None = None
    door_evidence: DiagnosisEvidence | None = None
    credential_evidence: DiagnosisEvidence | None = None
    policy_evidence: DiagnosisEvidence | None = None
    event_evidence: DiagnosisEvidence | None = None

    controller_status: str | None = None
    controller_health: str | None = None
    door_status: str | None = None
    lock_status: str | None = None
    credential_status: str | None = None
    policy_allowed: bool | None = None
    policy_time_range_count: int = 0
    latest_decision: str | None = None
    latest_deny_reason: str | None = None

    controller_abnormal: bool = False
    credential_abnormal: bool = False
    permission_denied: bool = False
    time_window_denied: bool = False
    door_lock_issue: bool = False

    missing: list[str] = field(default_factory=list)


class AccessDiagnosisRuleResult(BaseModel):
    """门禁候选根因规则的输出。"""

    model_config = ConfigDict(extra="forbid")

    label: AccessDiagnosisLabel
    explanation: str
    evidence_chain: list[str] = Field(default_factory=list)
    excluded_candidates: list[str] = Field(default_factory=list)
    troubleshooting_order: list[str] = Field(default_factory=list)
    matched_rule: str = ""


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("events") or []
    return [item for item in raw if isinstance(item, dict)]


def _latest_event(payload: dict[str, Any]) -> dict[str, Any]:
    events = _events(payload)
    return events[0] if events else {}


def _build_result(
    label: AccessDiagnosisLabel,
    matched_rule: str,
    evidence_chain: list[str],
) -> AccessDiagnosisRuleResult:
    excluded = [
        f"{other.value}：{explanation}"
        for other, explanation in LABEL_EXPLANATIONS.items()
        if other is not label
    ]
    return AccessDiagnosisRuleResult(
        label=label,
        explanation=LABEL_EXPLANATIONS[label],
        evidence_chain=evidence_chain,
        excluded_candidates=excluded,
        troubleshooting_order=list(TROUBLESHOOTING_ORDER[label]),
        matched_rule=matched_rule,
    )


def extract_access_facts(case: SecurityDiagnosisCase) -> AccessFacts:
    """从诊断 Evidence 中提取门禁事实。

    规则只读 Evidence payload。由于门禁凭证和人员标识在领域层已脱敏，
    这里不能依赖 credential_id / person_id 判断根因。
    """
    facts = AccessFacts()

    for item in case.evidence:
        if item.evidence_type is EvidenceType.ACCESS_CONTROLLER:
            facts.controller_evidence = item
        elif item.evidence_type is EvidenceType.ACCESS_DOOR:
            facts.door_evidence = item
        elif item.evidence_type is EvidenceType.ACCESS_CREDENTIAL:
            facts.credential_evidence = item
        elif item.evidence_type is EvidenceType.ACCESS_POLICY:
            facts.policy_evidence = item
        elif item.evidence_type is EvidenceType.ACCESS_EVENT:
            facts.event_evidence = item

    if facts.controller_evidence is None:
        facts.missing.append("access_controller")
    if facts.door_evidence is None:
        facts.missing.append("access_door")
    if facts.credential_evidence is None:
        facts.missing.append("access_credential")
    if facts.policy_evidence is None:
        facts.missing.append("access_policy")
    if facts.event_evidence is None:
        facts.missing.append("access_event")

    if facts.controller_evidence is not None:
        payload = facts.controller_evidence.payload or {}
        facts.controller_status = _as_str(payload.get("status"))
        facts.controller_health = _as_str(payload.get("health"))
        facts.controller_abnormal = (
            facts.controller_status in _CONTROLLER_ABNORMAL_STATUSES
            or facts.controller_health in _CONTROLLER_ABNORMAL_HEALTH
        )

    if facts.door_evidence is not None:
        payload = facts.door_evidence.payload or {}
        facts.door_status = _as_str(payload.get("door_status"))
        facts.lock_status = _as_str(payload.get("lock_status"))
        facts.door_lock_issue = (
            facts.door_status in _DOOR_ABNORMAL_STATUSES
            or facts.lock_status in _DOOR_LOCK_ABNORMAL_STATUSES
            or bool(payload.get("has_lock_error", False))
        )

    if facts.credential_evidence is not None:
        payload = facts.credential_evidence.payload or {}
        facts.credential_status = _as_str(payload.get("status"))
        facts.credential_abnormal = facts.credential_status in _CREDENTIAL_ABNORMAL_STATUSES

    if facts.policy_evidence is not None:
        payload = facts.policy_evidence.payload or {}
        facts.policy_allowed = _as_bool(payload.get("allowed"))
        facts.policy_time_range_count = len(payload.get("time_ranges") or [])
        facts.permission_denied = facts.policy_allowed is False

    if facts.event_evidence is not None:
        payload = facts.event_evidence.payload or {}
        latest = _latest_event(payload)
        facts.latest_decision = _as_str(latest.get("decision"))
        facts.latest_deny_reason = _as_str(latest.get("deny_reason"))

        facts.controller_abnormal = facts.controller_abnormal or (
            facts.latest_decision == "timeout"
            or facts.latest_deny_reason in _CONTROLLER_DENY_REASONS
        )
        facts.credential_abnormal = facts.credential_abnormal or (
            facts.latest_deny_reason in _CREDENTIAL_DENY_REASONS
        )
        facts.door_lock_issue = facts.door_lock_issue or (
            facts.latest_deny_reason in _DOOR_DENY_REASONS
        )
        facts.time_window_denied = facts.latest_deny_reason in _TIME_WINDOW_DENY_REASONS
        facts.permission_denied = facts.permission_denied or (
            facts.latest_deny_reason in _PERMISSION_DENY_REASONS
        )

    return facts


def infer_access_card_failed_label(case: SecurityDiagnosisCase) -> AccessDiagnosisRuleResult:
    """根据诊断 Evidence 推断门禁刷卡异常候选根因。

    判定顺序遵循"先基础设施、再硬件、再凭证、再权限、再时段"：

    1. 控制器离线 / 超时 -> controller_offline_or_no_response；
    2. 门锁 / 门磁 / 门状态异常 -> door_lock_or_sensor_issue；
    3. 凭证冻结、过期、挂失或无效 -> credential_invalid_or_frozen；
    4. 明确无门权限 -> permission_not_granted；
    5. 授权时段不覆盖刷卡时间 -> access_time_window_denied；
    6. 缺少关键事实或无法区分 -> insufficient_access_evidence。
    """
    facts = extract_access_facts(case)
    evidence_chain = [
        item.evidence_id
        for item in (
            facts.controller_evidence,
            facts.door_evidence,
            facts.credential_evidence,
            facts.policy_evidence,
            facts.event_evidence,
        )
        if item is not None
    ]

    if facts.event_evidence is None:
        reason = "、".join(facts.missing) or "access_event"
        return _build_result(
            AccessDiagnosisLabel.INSUFFICIENT_ACCESS_EVIDENCE,
            "R0 缺少关键门禁事件事实",
            [f"缺少 {reason}，仅收集到：{evidence_chain or ['（无）']}"],
        )

    if facts.controller_abnormal:
        return _build_result(
            AccessDiagnosisLabel.CONTROLLER_OFFLINE_OR_NO_RESPONSE,
            "R1 控制器离线 / 无响应",
            evidence_chain,
        )

    if facts.door_lock_issue:
        return _build_result(
            AccessDiagnosisLabel.DOOR_LOCK_OR_SENSOR_ISSUE,
            "R2 门锁 / 门磁异常",
            evidence_chain,
        )

    if facts.credential_abnormal:
        return _build_result(
            AccessDiagnosisLabel.CREDENTIAL_INVALID_OR_FROZEN,
            "R3 凭证状态异常",
            evidence_chain,
        )

    if facts.permission_denied:
        return _build_result(
            AccessDiagnosisLabel.PERMISSION_NOT_GRANTED,
            "R4 无目标门权限",
            evidence_chain,
        )

    if facts.time_window_denied:
        return _build_result(
            AccessDiagnosisLabel.ACCESS_TIME_WINDOW_DENIED,
            "R5 授权时段不覆盖刷卡时间",
            evidence_chain,
        )

    detail = (
        f"controller_status={facts.controller_status}, "
        f"door_status={facts.door_status}, "
        f"lock_status={facts.lock_status}, "
        f"credential_status={facts.credential_status}, "
        f"policy_allowed={facts.policy_allowed}, "
        f"latest_deny_reason={facts.latest_deny_reason}"
    )
    return _build_result(
        AccessDiagnosisLabel.INSUFFICIENT_ACCESS_EVIDENCE,
        "R6 证据不足以唯一确定候选根因",
        evidence_chain or [detail],
    )
