"""把重复设备事实解析为与插入顺序无关的确定性视图。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from security_diagnosis_harness.domain.common import canonical_json
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceType,
    Reliability,
)

_RELIABILITY_RANK = {
    Reliability.LOW: 0,
    Reliability.MEDIUM: 1,
    Reliability.HIGH: 2,
}


@dataclass(frozen=True)
class ResolvedEvidence:
    """按类型解析后的单值事实与无法自动消解的冲突。"""

    selected: dict[EvidenceType, DiagnosisEvidence]
    conflicts: dict[EvidenceType, tuple[DiagnosisEvidence, ...]]

    @property
    def conflicting_types(self) -> tuple[EvidenceType, ...]:
        return tuple(sorted(self.conflicts, key=lambda item: item.value))


def resolve_evidence(
    evidence: Iterable[DiagnosisEvidence],
    evidence_types: Iterable[EvidenceType],
) -> ResolvedEvidence:
    """按可靠性、采集时间和 ID 解析单值 Evidence。

    规则：

    1. 低可靠性事实不能覆盖同类更高可靠性事实；
    2. 同可靠性选择最新 captured_at；
    3. 同可靠性、同最新时间存在不同 payload 时标记冲突，不任意选取；
    4. 相同 payload 的重复事实用 evidence_id 作稳定 tie-break。
    """
    relevant = frozenset(evidence_types)
    grouped: dict[EvidenceType, list[DiagnosisEvidence]] = {}
    for item in evidence:
        if item.evidence_type in relevant:
            grouped.setdefault(item.evidence_type, []).append(item)

    selected: dict[EvidenceType, DiagnosisEvidence] = {}
    conflicts: dict[EvidenceType, tuple[DiagnosisEvidence, ...]] = {}
    for evidence_type, items in grouped.items():
        best_reliability = max(_RELIABILITY_RANK[item.reliability] for item in items)
        reliable = [
            item for item in items if _RELIABILITY_RANK[item.reliability] == best_reliability
        ]
        newest_timestamp = max(_timestamp(item.captured_at) for item in reliable)
        newest = [
            item for item in reliable if _timestamp(item.captured_at) == newest_timestamp
        ]
        payloads = {canonical_json(item.payload) for item in newest}
        if len(payloads) > 1:
            conflicts[evidence_type] = tuple(
                sorted(newest, key=lambda item: item.evidence_id)
            )
            continue
        selected[evidence_type] = max(newest, key=lambda item: item.evidence_id)

    return ResolvedEvidence(selected=selected, conflicts=conflicts)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).timestamp()


__all__ = ["ResolvedEvidence", "resolve_evidence"]
