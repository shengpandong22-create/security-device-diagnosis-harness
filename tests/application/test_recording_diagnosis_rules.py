"""录像缺失 / 异常候选根因规则验收。

规则只输出候选根因，不产出 confirmed，也不绕过 CitationPolicy。
测试直接基于 SecurityDiagnosisCase.evidence 的 payload 推断，不读取样例 JSON。
"""

from __future__ import annotations

from security_diagnosis_harness.application.recording_diagnosis_rules import (
    RecordingDiagnosisLabel,
    extract_recording_facts,
    infer_recording_missing_label,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)

DIAG_ID = "diag-rec-1"

FULL_DAY_RANGES = [{"start": "00:00:00", "end": "00:00:00", "weekdays": [1, 2, 3, 4, 5, 6, 7]}]
DAYTIME_RANGES = [{"start": "09:00:00", "end": "18:00:00", "weekdays": [1, 2, 3, 4, 5]}]
PLAYBACK_WINDOW = ("2026-09-08T00:00:00+08:00", "2026-09-09T00:00:00+08:00")


def _evidence(evidence_type: EvidenceType, payload: dict) -> DiagnosisEvidence:
    return DiagnosisEvidence(
        diagnosis_id=DIAG_ID,
        evidence_type=evidence_type,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary=f"{evidence_type.value} 证据",
        payload=payload,
        reliability=Reliability.HIGH,
    )


def _plan(status: str = "enabled", time_ranges: list[dict] | None = None) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.RECORDING_PLAN,
        {
            "status": status,
            "mode": "event",
            "time_ranges": time_ranges if time_ranges is not None else FULL_DAY_RANGES,
            "retention_days": 30,
        },
    )


def _storage(
    status: str = "normal",
    free_gb: int = 2000,
    total_gb: int = 4000,
    last_error: str = "",
) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.STORAGE_STATUS,
        {
            "status": status,
            "total_gb": total_gb,
            "free_gb": free_gb,
            "used_percent": round((total_gb - free_gb) / total_gb * 100, 1),
            "last_error": last_error or ("" if status == "normal" else "storage abnormal"),
        },
    )


def _continuous_plan() -> DiagnosisEvidence:
    """计划启用 + 连续录像 + 全天覆盖。"""
    return _evidence(
        EvidenceType.RECORDING_PLAN,
        {
            "status": "enabled",
            "mode": "continuous",
            "time_ranges": FULL_DAY_RANGES,
            "retention_days": 30,
        },
    )


def _storage_used_percent_only(used_percent: float) -> DiagnosisEvidence:
    """模拟只上报使用率百分比、不给 total_gb / free_gb 的平台。"""
    return _evidence(
        EvidenceType.STORAGE_STATUS,
        {
            "status": "normal",
            "used_percent": used_percent,
            "last_error": "",
        },
    )


def _playback(
    status: str = "missing",
    file_count: int = 0,
    playable: bool = False,
    failure_reason: str = "",
) -> DiagnosisEvidence:
    start_at, end_at = PLAYBACK_WINDOW
    return _evidence(
        EvidenceType.PLAYBACK_CHECK,
        {
            "status": status,
            "file_count": file_count,
            "playable": playable,
            "failure_reason": failure_reason,
            "start_at": start_at,
            "end_at": end_at,
        },
    )


def _case(*evidence: DiagnosisEvidence) -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=DIAG_ID,
        fault_type=SecurityFaultType.RECORDING_MISSING,
        device_id="cam-1",
        reporter="tester",
        evidence=list(evidence),
    )


# ---------------------------------------------------------------- 各候选根因识别
def test_recording_plan_disabled_detected():
    case = _case(_plan(status="disabled"), _storage(), _playback())
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.RECORDING_PLAN_DISABLED


def test_recording_schedule_gap_detected_with_empty_ranges():
    case = _case(_plan(time_ranges=[]), _storage(), _playback())
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.RECORDING_SCHEDULE_GAP


def test_recording_schedule_gap_detected_when_window_not_covered():
    plan = _plan(time_ranges=DAYTIME_RANGES)
    case = _case(plan, _storage(), _playback())
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.RECORDING_SCHEDULE_GAP


def test_storage_full_detected_as_capacity_issue():
    case = _case(_plan(), _storage(status="full", free_gb=40, total_gb=4000), _playback())
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.STORAGE_CAPACITY_OR_POOL_ISSUE


def test_storage_offline_detected_as_capacity_issue():
    case = _case(_plan(), _storage(status="offline", last_error="node unreachable"), _playback())
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.STORAGE_CAPACITY_OR_POOL_ISSUE


def test_recording_rules_detect_storage_low_capacity_by_used_percent_only():
    """只用 used_percent 口径也能判定容量不足（无 total_gb / free_gb）。"""
    case = _case(_continuous_plan(), _storage_used_percent_only(96.0), _playback())
    facts = extract_recording_facts(case)
    assert facts.storage_capacity_low is True

    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.STORAGE_CAPACITY_OR_POOL_ISSUE


def test_recording_rules_do_not_mark_storage_low_when_used_percent_has_enough_free_space():
    """used_percent=60（剩余 40%）不应被误判为容量不足。"""
    case = _case(
        _continuous_plan(),
        _storage_used_percent_only(60.0),
        _playback(status="index_missing", file_count=12),
    )
    facts = extract_recording_facts(case)
    assert facts.storage_capacity_low is False

    result = infer_recording_missing_label(case)
    assert result.label is not RecordingDiagnosisLabel.STORAGE_CAPACITY_OR_POOL_ISSUE
    assert result.label is RecordingDiagnosisLabel.PLAYBACK_INDEX_OR_FILE_ISSUE


def test_playback_index_missing_detected_as_file_issue():
    case = _case(_plan(), _storage(), _playback(status="index_missing", file_count=12))
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.PLAYBACK_INDEX_OR_FILE_ISSUE


# ---------------------------------------------------------------- 证据不足
def test_insufficient_when_plan_missing():
    case = _case(_storage(), _playback())
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.INSUFFICIENT_RECORDING_EVIDENCE


def test_insufficient_when_playback_missing():
    case = _case(_plan(), _storage())
    result = infer_recording_missing_label(case)
    assert result.label is RecordingDiagnosisLabel.INSUFFICIENT_RECORDING_EVIDENCE


# ---------------------------------------------------------------- 规则不产生 confirmed
def test_rules_never_emitted_confirmed_label():
    for label in RecordingDiagnosisLabel:
        assert "confirmed" not in label.value

    case = _case(_plan(status="disabled"), _storage(), _playback())
    result = infer_recording_missing_label(case)
    assert result.label is not None
    assert result.label in set(RecordingDiagnosisLabel)


# ---------------------------------------------------------------- 输出结构
def test_evidence_chain_contains_key_evidence():
    plan = _plan(status="disabled")
    storage = _storage()
    playback = _playback()
    case = _case(plan, storage, playback)
    result = infer_recording_missing_label(case)

    assert plan.evidence_id in result.evidence_chain
    assert storage.evidence_id in result.evidence_chain
    assert playback.evidence_id in result.evidence_chain


def test_troubleshooting_order_nonempty():
    case = _case(_plan(status="disabled"), _storage(), _playback())
    result = infer_recording_missing_label(case)
    assert result.troubleshooting_order


def test_excluded_candidates_explains():
    case = _case(_plan(status="disabled"), _storage(), _playback())
    result = infer_recording_missing_label(case)
    assert result.excluded_candidates
    assert all(
        "recording_plan_disabled" not in item for item in result.excluded_candidates
    )


def test_extract_recording_facts_reads_payload():
    plan = _plan(time_ranges=DAYTIME_RANGES)
    storage = _storage(status="full", free_gb=40, total_gb=4000)
    playback = _playback()
    case = _case(plan, storage, playback)
    facts = extract_recording_facts(case)

    assert facts.recording_plan_evidence is not None
    assert facts.storage_status_evidence is not None
    assert facts.playback_check_evidence is not None
    assert facts.plan_status == "enabled"
    assert facts.storage_capacity_low is True
    assert facts.playback_status == "missing"
    assert facts.playback_start_at is not None
    assert facts.playback_end_at is not None


# ---------------------------------------------------------------- 报告
def test_report_contains_recording_candidate_and_summaries():
    from security_diagnosis_harness.application.reports import render_markdown_report

    case = _case(_plan(status="disabled"), _storage(), _playback())
    markdown = render_markdown_report(case)

    assert "录像诊断（候选）" in markdown
    assert "recording_plan_disabled" in markdown
    assert "录像计划摘要" in markdown
    assert "存储状态摘要" in markdown
    assert "回放检查摘要" in markdown


def test_report_redacts_sensitive_payload_values():
    from security_diagnosis_harness.domain.device import REDACTED_VALUE

    sensitive_playback = _playback()
    sensitive_playback.payload = {**sensitive_playback.payload, "password": "leak-me-not"}
    case = _case(_plan(status="disabled"), _storage(), sensitive_playback)

    from security_diagnosis_harness.application.reports import render_markdown_report

    markdown = render_markdown_report(case)

    assert "leak-me-not" not in markdown
    assert REDACTED_VALUE in markdown
