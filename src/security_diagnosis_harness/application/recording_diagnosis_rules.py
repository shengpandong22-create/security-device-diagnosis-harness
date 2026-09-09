"""录像缺失 / 录像异常候选根因规则。

这是一层确定性辅助判断，不是 LLM 的替代品：

- 输入是当前诊断已经落地的 Evidence（录像计划、存储状态、回放检查等）；
- 输出只是"候选标签 + 证据链解释 + 排查顺序 + 排除项"；
- 它永远不会直接产生 confirmed，也不绕过 CitationPolicy；
- 它基于 `SecurityDiagnosisCase.evidence` 的 payload 推断，不直接读取样例 JSON。

候选标签（RecordingDiagnosisLabel）：

- recording_plan_disabled：录像计划被禁用；
- recording_schedule_gap：计划已启用但查询时间段不在计划覆盖范围内；
- storage_capacity_or_pool_issue：存储池满 / 离线 / 降级导致无法写入或读取；
- playback_index_or_file_issue：计划与存储正常但回放索引缺失或文件损坏；
- insufficient_recording_evidence：缺少关键录像事实，不能给出可靠候选。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence, EvidenceType


class RecordingDiagnosisLabel(StrEnum):
    """录像缺失 / 异常的候选根因标签。"""

    RECORDING_PLAN_DISABLED = "recording_plan_disabled"
    RECORDING_SCHEDULE_GAP = "recording_schedule_gap"
    STORAGE_CAPACITY_OR_POOL_ISSUE = "storage_capacity_or_pool_issue"
    PLAYBACK_INDEX_OR_FILE_ISSUE = "playback_index_or_file_issue"
    INSUFFICIENT_RECORDING_EVIDENCE = "insufficient_recording_evidence"


# 各类候选根因的人类可读说明，用于报告展示。
LABEL_EXPLANATIONS: dict[RecordingDiagnosisLabel, str] = {
    RecordingDiagnosisLabel.RECORDING_PLAN_DISABLED: (
        "录像计划被禁用，设备未安排录像任务，因此目标时间段没有任何录像文件"
    ),
    RecordingDiagnosisLabel.RECORDING_SCHEDULE_GAP: (
        "录像计划已启用，但查询时间段不在录像计划覆盖范围内，计划未覆盖该时段"
    ),
    RecordingDiagnosisLabel.STORAGE_CAPACITY_OR_POOL_ISSUE: (
        "存储池已满 / 离线 / 降级，导致新录像无法写入或历史录像无法读取"
    ),
    RecordingDiagnosisLabel.PLAYBACK_INDEX_OR_FILE_ISSUE: (
        "录像计划与存储均正常，但回放索引缺失或录像文件损坏，无法定位回放时间点"
    ),
    RecordingDiagnosisLabel.INSUFFICIENT_RECORDING_EVIDENCE: (
        "缺少关键录像事实（录像计划 / 回放检查），无法给出可靠根因候选"
    ),
}

# 各类候选根因的排查顺序，用于报告展示。
TROUBLESHOOTING_ORDER: dict[RecordingDiagnosisLabel, list[str]] = {
    RecordingDiagnosisLabel.RECORDING_PLAN_DISABLED: [
        "确认录像计划是否被管理员误禁用",
        "在设备侧或平台侧重新启用录像计划",
        "确认计划启用后目标时间段开始产生录像",
        "重新发起回放检查确认录像可查",
    ],
    RecordingDiagnosisLabel.RECORDING_SCHEDULE_GAP: [
        "确认录像计划的时间段与星期是否覆盖查询窗口",
        "调整录像计划时间段，使其覆盖需要录像的时段",
        "确认计划保存后设备重新按新计划录像",
        "重新发起回放检查确认录像可查",
    ],
    RecordingDiagnosisLabel.STORAGE_CAPACITY_OR_POOL_ISSUE: [
        "确认存储池容量与使用率，清理或扩容存储",
        "确认存储节点在线状态与网络连接",
        "确认存储池恢复正常后录像写入是否恢复",
        "重新发起回放检查确认历史录像可读",
    ],
    RecordingDiagnosisLabel.PLAYBACK_INDEX_OR_FILE_ISSUE: [
        "确认录像文件是否存在且大小正常",
        "触发录像索引重建并观察结果",
        "如索引重建失败，考虑从备份或相邻时间段恢复",
        "重新发起回放检查确认可定位回放时间点",
    ],
    RecordingDiagnosisLabel.INSUFFICIENT_RECORDING_EVIDENCE: [
        "补充采集录像计划、存储状态与回放检查事实",
        "确认只读工具是否全部执行成功",
        "补充现场描述后重新运行诊断",
    ],
}

# 存储状态中表示容量 / 池异常的枚举值（与 domain.recording.StorageStatus 对应）。
_STORAGE_ABNORMAL_STATUSES: frozenset[str] = frozenset({"full", "offline", "degraded"})

# 回放状态中属于"无文件可回放"的枚举值（与 domain.recording.PlaybackCheckStatus 对应）。
_PLAYBACK_MISSING_STATUSES: frozenset[str] = frozenset({"missing"})

# 回放状态中属于"有文件但索引 / 文件损坏"的枚举值。
_PLAYBACK_FILE_ISSUE_STATUSES: frozenset[str] = frozenset({"index_missing", "corrupted"})

# 当剩余可用容量占比低于该阈值时判定为容量不足。
_LOW_FREE_RATIO_THRESHOLD = 0.1

# used_percent 口径下的等价阈值：剩余可用百分比（100 - used_percent）低于该值判定为容量不足。
_LOW_FREE_PERCENT_THRESHOLD = _LOW_FREE_RATIO_THRESHOLD * 100


@dataclass
class RecordingFacts:
    """从 Evidence 中提取出的录像事实。"""

    recording_plan_evidence: DiagnosisEvidence | None = None
    storage_status_evidence: DiagnosisEvidence | None = None
    playback_check_evidence: DiagnosisEvidence | None = None

    plan_status: str | None = None
    plan_time_ranges: list[dict[str, Any]] = field(default_factory=list)
    plan_active: bool = False
    schedule_gap: bool = False

    storage_status_value: str | None = None
    storage_capacity_low: bool = False

    playback_status: str | None = None
    playback_file_count: int = 0
    playback_playable: bool = False
    playback_failure_reason: str | None = None
    playback_start_at: datetime | None = None
    playback_end_at: datetime | None = None

    missing: list[str] = field(default_factory=list)


class RecordingDiagnosisRuleResult(BaseModel):
    """候选根因规则的输出。"""

    model_config = ConfigDict(extra="forbid")

    label: RecordingDiagnosisLabel
    explanation: str
    evidence_chain: list[str] = Field(default_factory=list)
    excluded_candidates: list[str] = Field(default_factory=list)
    troubleshooting_order: list[str] = Field(default_factory=list)
    matched_rule: str = ""


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _parse_minutes(hms: str) -> float:
    """把 "HH:MM:SS" 解析为当日分钟数；解析失败返回 0。"""
    try:
        parts = [int(p) for p in str(hms).split(":")]
    except (TypeError, ValueError):
        return 0.0
    if len(parts) >= 2:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2]) if len(parts) > 2 else 0.0
        return hours * 60 + minutes + seconds / 60
    if len(parts) == 1:
        return float(parts[0])
    return 0.0


def _plan_covers_window(
    time_ranges: list[dict[str, Any]],
    start_at: datetime,
    end_at: datetime,
) -> bool:
    """判断录像计划的 time_ranges 是否完全覆盖查询窗口 [start_at, end_at]。

    仅用于"计划已启用但查询时间段不在计划内"的判定。start==end=="00:00:00"
    视为全天覆盖。返回 True 表示窗口被完整覆盖，False 表示存在覆盖缺口。
    """
    if not time_ranges or start_at >= end_at:
        return False

    segments: list[tuple[set[int], float, float, bool]] = []
    for tr in time_ranges:
        weekdays = set(int(w) for w in (tr.get("weekdays") or list(range(1, 8))))
        start_min = _parse_minutes(tr.get("start", "00:00:00"))
        end_min = _parse_minutes(tr.get("end", "00:00:00"))
        full_day = abs(end_min - start_min) < 1e-9
        segments.append((weekdays, start_min, end_min, full_day))

    cursor = start_at
    total_minutes = 0.0
    covered_minutes = 0.0
    while cursor < end_at:
        day_start = cursor
        day_midnight = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_midnight + timedelta(days=1)
        day_end = min(next_day, end_at)
        weekday = cursor.isoweekday()

        covered = 0.0
        for weekdays, start_min, end_min, full_day in segments:
            if weekday not in weekdays:
                continue
            if full_day:
                covered += (day_end - day_start).total_seconds() / 60
            else:
                seg_start = day_midnight + timedelta(minutes=start_min)
                seg_end = day_midnight + timedelta(minutes=end_min)
                cov_start = max(seg_start, day_start)
                cov_end = min(seg_end, day_end)
                if cov_end > cov_start:
                    covered += (cov_end - cov_start).total_seconds() / 60

        total_minutes += (day_end - day_start).total_seconds() / 60
        covered_minutes += covered
        cursor = day_end

    if total_minutes <= 0:
        return False
    return covered_minutes / total_minutes >= 0.999


def extract_recording_facts(case: SecurityDiagnosisCase) -> RecordingFacts:
    """从诊断 Evidence 中提取录像事实。

    规则只读取 Evidence payload，不直接读取样例 JSON。
    """
    facts = RecordingFacts()
    plan_evidence: DiagnosisEvidence | None = None
    storage_evidence: DiagnosisEvidence | None = None
    playback_evidence: DiagnosisEvidence | None = None

    for item in case.evidence:
        if item.evidence_type is EvidenceType.RECORDING_PLAN:
            plan_evidence = item
        elif item.evidence_type is EvidenceType.STORAGE_STATUS:
            storage_evidence = item
        elif item.evidence_type is EvidenceType.PLAYBACK_CHECK:
            playback_evidence = item

    facts.recording_plan_evidence = plan_evidence
    facts.storage_status_evidence = storage_evidence
    facts.playback_check_evidence = playback_evidence

    if plan_evidence is None:
        facts.missing.append("recording_plan")
    if storage_evidence is None:
        facts.missing.append("storage_status")
    if playback_evidence is None:
        facts.missing.append("playback_check")

    if plan_evidence is not None:
        payload = plan_evidence.payload or {}
        facts.plan_status = _as_str(payload.get("status"))
        facts.plan_time_ranges = list(payload.get("time_ranges") or [])
        enabled = facts.plan_status == "enabled"
        has_ranges = len(facts.plan_time_ranges) > 0
        facts.plan_active = enabled and has_ranges
        facts.schedule_gap = enabled and not has_ranges

    if storage_evidence is not None:
        payload = storage_evidence.payload or {}
        facts.storage_status_value = _as_str(payload.get("status"))
        total_gb = _as_int(payload.get("total_gb"))
        free_gb = _as_int(payload.get("free_gb"))
        used_percent = _as_float(payload.get("used_percent"))
        status_low = facts.storage_status_value in _STORAGE_ABNORMAL_STATUSES
        ratio_low = (
            total_gb is not None
            and free_gb is not None
            and total_gb > 0
            and (free_gb / total_gb) < _LOW_FREE_RATIO_THRESHOLD
        )
        # 部分平台只上报使用率百分比，不给 total_gb / free_gb，
        # 此时按剩余可用百分比（100 - used_percent）判断容量是否不足。
        used_low = (
            used_percent is not None
            and (100.0 - used_percent) < _LOW_FREE_PERCENT_THRESHOLD
        )
        facts.storage_capacity_low = status_low or ratio_low or used_low

    if playback_evidence is not None:
        payload = playback_evidence.payload or {}
        facts.playback_status = _as_str(payload.get("status"))
        facts.playback_file_count = _as_int(payload.get("file_count")) or 0
        facts.playback_playable = bool(payload.get("playable", False))
        facts.playback_failure_reason = _as_str(payload.get("failure_reason"))
        raw_start = payload.get("start_at")
        raw_end = payload.get("end_at")
        try:
            facts.playback_start_at = (
                datetime.fromisoformat(raw_start) if isinstance(raw_start, str) else None
            )
        except ValueError:
            facts.playback_start_at = None
        try:
            facts.playback_end_at = (
                datetime.fromisoformat(raw_end) if isinstance(raw_end, str) else None
            )
        except ValueError:
            facts.playback_end_at = None

    return facts


def _build_result(
    label: RecordingDiagnosisLabel,
    facts: RecordingFacts,
    matched_rule: str,
    evidence_chain: list[str],
) -> RecordingDiagnosisRuleResult:
    excluded = [
        f"{other.value}：{explanation}"
        for other, explanation in LABEL_EXPLANATIONS.items()
        if other is not label
    ]
    return RecordingDiagnosisRuleResult(
        label=label,
        explanation=LABEL_EXPLANATIONS[label],
        evidence_chain=evidence_chain,
        excluded_candidates=excluded,
        troubleshooting_order=list(TROUBLESHOOTING_ORDER[label]),
        matched_rule=matched_rule,
    )


def infer_recording_missing_label(case: SecurityDiagnosisCase) -> RecordingDiagnosisRuleResult:
    """根据诊断 Evidence 推断录像缺失 / 异常的候选根因标签。

    规则只输出候选，不产生 confirmed，也不修改任何诊断状态。

    判定顺序（最具体优先）：

    1. 录像计划被禁用且回放缺失 -> recording_plan_disabled；
    2. 存储池异常且回放缺失 -> storage_capacity_or_pool_issue；
    3. 计划已启用但查询窗口未被计划覆盖且回放缺失 -> recording_schedule_gap；
    4. 计划正常、存储可用、回放索引缺失 / 文件损坏 -> playback_index_or_file_issue；
    5. 缺少关键录像事实 -> insufficient_recording_evidence。
    """
    facts = extract_recording_facts(case)

    evidence_chain: list[str] = []
    if facts.recording_plan_evidence is not None:
        evidence_chain.append(facts.recording_plan_evidence.evidence_id)
    if facts.storage_status_evidence is not None:
        evidence_chain.append(facts.storage_status_evidence.evidence_id)
    if facts.playback_check_evidence is not None:
        evidence_chain.append(facts.playback_check_evidence.evidence_id)

    # 缺少录像计划或回放检查，无法可靠推断根因。
    if facts.recording_plan_evidence is None or facts.playback_check_evidence is None:
        reason = "、".join(facts.missing) or "关键录像事实"
        return _build_result(
            RecordingDiagnosisLabel.INSUFFICIENT_RECORDING_EVIDENCE,
            facts,
            "R0 缺少关键录像事实",
            [f"缺少 {reason}，仅收集到：{evidence_chain or ['（无）']}"],
        )

    playback_missing = facts.playback_status in _PLAYBACK_MISSING_STATUSES

    # 规则 1：录像计划被禁用。
    if facts.plan_status == "disabled" and playback_missing:
        return _build_result(
            RecordingDiagnosisLabel.RECORDING_PLAN_DISABLED,
            facts,
            "R1 录像计划禁用",
            evidence_chain,
        )

    # 规则 2：存储池异常导致回放缺失。
    if facts.storage_capacity_low and playback_missing:
        return _build_result(
            RecordingDiagnosisLabel.STORAGE_CAPACITY_OR_POOL_ISSUE,
            facts,
            "R2 存储容量 / 存储池异常",
            evidence_chain,
        )

    # 规则 3：计划已启用但查询窗口不在计划覆盖范围内。
    schedule_gap = facts.schedule_gap
    if (
        not schedule_gap
        and facts.playback_start_at is not None
        and facts.playback_end_at is not None
    ):
        schedule_gap = not _plan_covers_window(
            facts.plan_time_ranges, facts.playback_start_at, facts.playback_end_at
        )
    if schedule_gap and playback_missing:
        return _build_result(
            RecordingDiagnosisLabel.RECORDING_SCHEDULE_GAP,
            facts,
            "R3 录像计划时间段缺口",
            evidence_chain,
        )

    # 规则 4：计划与存储正常，但回放索引缺失 / 文件损坏。
    storage_available = not facts.storage_capacity_low
    if (
        facts.plan_active
        and storage_available
        and facts.playback_status in _PLAYBACK_FILE_ISSUE_STATUSES
    ):
        return _build_result(
            RecordingDiagnosisLabel.PLAYBACK_INDEX_OR_FILE_ISSUE,
            facts,
            "R4 回放索引 / 文件损坏",
            evidence_chain,
        )

    # 规则 5：证据不足以唯一确定候选根因。
    detail = (
        f"plan_status={facts.plan_status}, "
        f"storage_status={facts.storage_status_value}, "
        f"playback_status={facts.playback_status}"
    )
    return _build_result(
        RecordingDiagnosisLabel.INSUFFICIENT_RECORDING_EVIDENCE,
        facts,
        "R5 证据不足以唯一确定候选根因",
        evidence_chain or [detail],
    )
