"""Phase 10A simulator_e2e 影子历史与逐项 Gate。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from security_diagnosis_harness.evaluation import (
    CORE_SHADOW_RATES,
    SAFETY_BLOCKERS,
    JsonShadowHistory,
    ShadowComparabilityError,
    ShadowGateConfigurationError,
    ShadowGatePolicy,
    ShadowHistoryError,
    ShadowMetrics,
    ShadowRunIdentity,
    ShadowRunSummary,
    ShadowScenarioSummary,
    SimulatorShadowRun,
    compare_shadow_runs,
)

CREATED_AT = datetime(2026, 9, 14, tzinfo=UTC)


def _identity(commit: str, **changes: object) -> ShadowRunIdentity:
    return ShadowRunIdentity(
        code_commit=commit,
        suite_name="simulator-shadow",
        suite_version="1.0.0",
        configuration_hash="1" * 64,
        scenario_set_hash="2" * 64,
    ).model_copy(update=changes)


def _scenarios(**counters: int) -> tuple[ShadowScenarioSummary, ...]:
    base = {"completed": True, "controlled_degradation": True}
    return (
        ShadowScenarioSummary(scenario_id="success", **base),
        ShadowScenarioSummary(scenario_id="degraded", **base, **counters),
    )


def _metrics(
    total: int, completed: int, controlled_degradation_cases: int, **counters: int
) -> ShadowMetrics:
    return ShadowMetrics(
        total=total,
        completed=completed,
        controlled_degradation_cases=controlled_degradation_cases,
        evidence_violations=counters.get("evidence_violations", 0),
        p0_findings=counters.get("p0_findings", 0),
        sensitive_leaks=counters.get("sensitive_leaks", 0),
        device_writes=counters.get("device_writes", 0),
        external_notifications=counters.get("external_notifications", 0),
    )


def _run(
    commit: str, *, run_id: str = "shadow-run", **identity_changes: object
) -> SimulatorShadowRun:
    return SimulatorShadowRun(
        run_id=run_id,
        created_at=CREATED_AT + timedelta(seconds=len(commit)),
        identity=_identity(commit, **identity_changes),
        scenarios=_scenarios(),
    )


def _summary(
    commit: str,
    *,
    run_id: str = "shadow-run",
    scenarios: tuple[ShadowScenarioSummary, ...] | None = None,
    **changes: object,
) -> ShadowRunSummary:
    return ShadowRunSummary.from_run(
        SimulatorShadowRun(
            run_id=run_id,
            created_at=CREATED_AT,
            identity=_identity(commit, **changes),
            scenarios=scenarios if scenarios is not None else _scenarios(),
        )
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"device_id": "camera-3f-001"},
        {"endpoint": "https://10.10.10.10/api"},
        {"credential_ref": "vault://camera/readonly"},
        {"api_key": "DO-NOT-PERSIST"},
        {"report_kind": "real_model"},
        {"adapter_kind": "authorized_device"},
    ],
)
def test_identity_rejects_identifiers_endpoints_credentials_and_other_report_kinds(payload):
    with pytest.raises(ValueError):
        ShadowRunIdentity(
            code_commit="a" * 40,
            suite_name="simulator-shadow",
            suite_version="1.0.0",
            configuration_hash="1" * 64,
            scenario_set_hash="2" * 64,
            **payload,
        )


def test_identity_extras_never_reach_persisted_history(tmp_path):
    path = tmp_path / "shadow-history.json"
    run = _run("a" * 40).model_copy(
        update={"identity": _identity("a" * 40, device_id="camera-3f-001")}
    )
    JsonShadowHistory(path).append(run)
    raw = path.read_text(encoding="utf-8")
    assert "device_id" not in raw
    assert "camera-3f-001" not in raw
    assert "endpoint" not in raw


def test_run_rejects_externally_supplied_aggregates():
    with pytest.raises(ValueError):
        SimulatorShadowRun(
            run_id="shadow-run",
            created_at=CREATED_AT,
            identity=_identity("a" * 40),
            scenarios=_scenarios(),
            metrics=_metrics(2, 2, 2),
        )


def test_metrics_are_recomputed_from_scenario_facts():
    scenarios = (
        ShadowScenarioSummary(
            scenario_id="a", completed=True, controlled_degradation=True, p0_findings=2
        ),
        ShadowScenarioSummary(scenario_id="b", completed=False, controlled_degradation=True),
    )
    run = SimulatorShadowRun(
        run_id="shadow-run",
        created_at=CREATED_AT,
        identity=_identity("a" * 40),
        scenarios=scenarios,
    )
    metrics = ShadowRunSummary.from_run(run).metrics
    assert metrics.total == 2
    assert metrics.completion_rate == 0.5
    assert metrics.controlled_degradation_coverage == 1.0
    assert metrics.p0_findings == 2


def test_metrics_reject_impossible_counts():
    with pytest.raises(ValueError):
        _metrics(1, 2, 1)
    with pytest.raises(ValueError):
        _metrics(1, 1, 2)
    empty = _metrics(0, 0, 0)
    assert empty.completion_rate == 0.0
    assert empty.controlled_degradation_coverage == 0.0


def test_naive_created_at_is_rejected():
    with pytest.raises(ValueError):
        SimulatorShadowRun(
            run_id="shadow-run",
            created_at=datetime(2026, 9, 14),
            identity=_identity("a" * 40),
        )


def test_first_run_is_persisted_as_baseline_summary(tmp_path):
    history = JsonShadowHistory(tmp_path / "shadow-history.json")
    record = history.append(_run("a" * 40))
    assert record.baseline_run_id is None
    assert record.gate_allowed is None
    assert record.blocked_by_safety is False
    assert history.load().records == (record,)


def test_history_json_excludes_scenarios_evidence_and_device_identifiers(tmp_path):
    path = tmp_path / "shadow-history.json"
    run = SimulatorShadowRun(
        run_id="shadow-run",
        created_at=CREATED_AT,
        identity=_identity("a" * 40),
        scenarios=(
            ShadowScenarioSummary(
                scenario_id="camera-3f-001", completed=True, controlled_degradation=True
            ),
        ),
    )
    JsonShadowHistory(path).append(run)
    raw = path.read_text(encoding="utf-8")
    for forbidden in ('"scenarios"', '"evidence"', '"conclusion"', "camera-3f-001"):
        assert forbidden not in raw
    assert '"metrics"' in raw
    assert not list(tmp_path.glob("*.tmp"))


def test_duplicate_run_id_is_rejected(tmp_path):
    history = JsonShadowHistory(tmp_path / "shadow-history.json")
    run = _run("a" * 40)
    history.append(run)
    with pytest.raises(ShadowHistoryError, match="已存在"):
        history.append(run)


def test_baseline_is_required_and_must_be_the_first_record(tmp_path):
    history = JsonShadowHistory(tmp_path / "shadow-history.json")
    with pytest.raises(ShadowHistoryError, match="不能声明"):
        history.append(_run("a" * 40), baseline=_run("c" * 40, run_id="unknown"))

    history.append(_run("a" * 40))
    with pytest.raises(ShadowHistoryError, match="必须提供"):
        history.append(_run("b" * 40, run_id="second"))


def test_baseline_must_be_persisted_with_matching_content_hash(tmp_path):
    history = JsonShadowHistory(tmp_path / "shadow-history.json")
    baseline = _run("a" * 40)
    history.append(baseline)
    tampered = baseline.model_copy(update={"scenarios": _scenarios(p0_findings=1)})
    with pytest.raises(ShadowHistoryError, match="哈希一致"):
        history.append(_run("b" * 40, run_id="second"), baseline=tampered)
    assert len(history.load().records) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"suite_version": "1.1.0"},
        {"suite_name": "another-suite"},
        {"configuration_hash": "3" * 64},
        {"scenario_set_hash": "4" * 64},
    ],
)
def test_incomparable_control_variables_never_enter_trend(tmp_path, changes):
    history = JsonShadowHistory(tmp_path / "shadow-history.json")
    baseline = _run("a" * 40)
    candidate = _run("b" * 40, run_id="second", **changes)
    history.append(baseline)
    with pytest.raises(ShadowComparabilityError, match="不可比") as excinfo:
        history.append(candidate, baseline=baseline)
    assert excinfo.value.changed_fields == tuple(changes)
    assert len(history.load().records) == 1


def test_code_commit_run_id_and_time_are_the_only_free_variables(tmp_path):
    history = JsonShadowHistory(tmp_path / "shadow-history.json")
    baseline = _run("a" * 40, run_id="baseline")
    candidate = _run("b" * 40, run_id="candidate")
    history.append(baseline)
    history.append(candidate, baseline=baseline)
    records = history.load().records
    assert records[0].summary.comparison_fingerprint == (
        records[1].summary.comparison_fingerprint
    )
    assert records[0].summary.run_content_hash != records[1].summary.run_content_hash
    # Baseline 记录不得被后续 Candidate 追加修改。
    assert records[0].baseline_run_id is None
    assert records[0].gate_allowed is None


def test_same_commit_or_same_run_id_is_not_comparable():
    with pytest.raises(ShadowGateConfigurationError, match="不同代码 commit"):
        compare_shadow_runs(_summary("a" * 40, run_id="one"), _summary("a" * 40, run_id="two"))
    with pytest.raises(ShadowGateConfigurationError, match="必须不同"):
        compare_shadow_runs(
            _summary("a" * 40, run_id="same"), _summary("b" * 40, run_id="same")
        )


@pytest.mark.parametrize(("metric", "label"), SAFETY_BLOCKERS)
def test_each_safety_blocker_blocks_gate_per_item(tmp_path, metric, label):
    history = JsonShadowHistory(tmp_path / "shadow-history.json")
    baseline = _run("a" * 40)
    candidate = SimulatorShadowRun(
        run_id="second",
        created_at=CREATED_AT + timedelta(seconds=3),
        identity=_identity("b" * 40),
        scenarios=_scenarios(**{metric: 1}),
    )
    history.append(baseline)
    record = history.append(candidate, baseline=baseline)
    assert record.gate_allowed is False
    assert record.blocked_by_safety is True
    assert any(label in reason for reason in record.blocking_reasons)
    report = compare_shadow_runs(
        history.load().records[0].summary, history.load().records[-1].summary
    )
    assert report.allowed is False
    assert report.blocked_by_safety is True
    blocked = {check.metric: check.blocked for check in report.safety_checks}
    assert blocked[metric] is True
    assert sum(blocked.values()) == 1


def test_gate_is_computed_per_item_not_from_a_single_boolean():
    report = compare_shadow_runs(_summary("a" * 40, run_id="one"), _summary("b" * 40, run_id="two"))
    assert [item.metric for item in report.metric_deltas] == [
        metric for metric, _ in CORE_SHADOW_RATES
    ]
    assert [item.metric for item in report.safety_checks] == [
        metric for metric, _ in SAFETY_BLOCKERS
    ]
    assert report.allowed is (not report.blocking_reasons)
    assert report.allowed is True
    assert report.blocked_by_safety is False


def test_core_rate_regression_blocks_without_safety_violation():
    baseline = _summary("a" * 40, run_id="one")
    candidate = _summary(
        "b" * 40,
        run_id="two",
        scenarios=(
            ShadowScenarioSummary(scenario_id="a", completed=True, controlled_degradation=False),
            ShadowScenarioSummary(scenario_id="b", completed=False, controlled_degradation=False),
        ),
    )
    report = compare_shadow_runs(baseline, candidate)
    assert report.allowed is False
    assert report.blocked_by_safety is False
    assert report.blocking_reasons == (
        "核心指标退化: completion_rate",
        "核心指标退化: controlled_degradation_coverage",
    )
    deltas = {item.metric: item for item in report.metric_deltas}
    assert deltas["completion_rate"].delta == -0.5
    assert deltas["completion_rate"].regressed is True


def test_tolerance_absorbs_small_rate_drop():
    baseline = _summary("a" * 40, run_id="one")
    candidate = _summary(
        "b" * 40,
        run_id="two",
        scenarios=(
            ShadowScenarioSummary(scenario_id="a", completed=True, controlled_degradation=True),
            ShadowScenarioSummary(scenario_id="b", completed=False, controlled_degradation=True),
        ),
    )
    strict = compare_shadow_runs(baseline, candidate)
    relaxed = compare_shadow_runs(
        baseline, candidate, ShadowGatePolicy(completion_rate_tolerance=0.5)
    )
    assert strict.allowed is False
    assert relaxed.allowed is True
    assert relaxed.blocking_reasons == ()


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"schema_version": "1.0.0", "records": [{"summary": {}}]}),
        json.dumps(
            {
                "schema_version": "1.0.0",
                "records": [
                    {
                        "summary": {
                            "run_id": "shadow-run",
                            "created_at": CREATED_AT.isoformat(),
                            "identity": _identity("a" * 40).model_dump(mode="json"),
                            "comparison_fingerprint": "1" * 64,
                            "run_content_hash": "2" * 64,
                            "metrics": {
                                "total": -1,
                                "completed": 0,
                                "controlled_degradation_cases": 0,
                                "evidence_violations": 0,
                                "p0_findings": 0,
                                "sensitive_leaks": 0,
                                "device_writes": 0,
                                "external_notifications": 0,
                            },
                        }
                    }
                ],
            }
        ),
    ],
)
def test_corrupt_or_invalid_history_is_rejected(tmp_path, content):
    path = tmp_path / "shadow-history.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ShadowHistoryError, match="协议"):
        JsonShadowHistory(path).load()


def test_missing_history_file_starts_empty(tmp_path):
    assert JsonShadowHistory(tmp_path / "shadow-history.json").load().records == ()


def test_history_writes_are_atomic_and_leave_no_temporary_file(tmp_path):
    path = tmp_path / "shadow-history.json"
    history = JsonShadowHistory(path)
    baseline = _run("a" * 40, run_id="baseline")
    history.append(baseline)
    history.append(_run("b" * 40, run_id="candidate"), baseline=baseline)
    assert not list(tmp_path.glob("*.tmp"))
    assert len(json.loads(path.read_text(encoding="utf-8"))["records"]) == 2


@pytest.mark.parametrize("run_id", ["camera 3f 001", "device/secret", "设备-001"])
def test_run_id_rejects_free_text_and_device_like_identifiers(run_id):
    with pytest.raises(ValueError):
        _run("a" * 40, run_id=run_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_content_hash", "f" * 64),
        ("comparison_fingerprint", "e" * 64),
    ],
)
def test_history_load_rejects_tampered_summary_hashes(tmp_path, field, value):
    path = tmp_path / "shadow-history.json"
    history = JsonShadowHistory(path)
    history.append(_run("a" * 40, run_id="baseline"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["summary"][field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ShadowHistoryError, match="协议"):
        history.load()


def test_history_load_rejects_tampered_gate_decision(tmp_path):
    path = tmp_path / "shadow-history.json"
    history = JsonShadowHistory(path)
    baseline = _run("a" * 40, run_id="baseline")
    history.append(baseline)
    history.append(_run("b" * 40, run_id="candidate"), baseline=baseline)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][1]["gate_allowed"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ShadowHistoryError, match="协议"):
        history.load()


def test_custom_policy_round_trips_and_reloads(tmp_path):
    path = tmp_path / "shadow-history.json"
    history = JsonShadowHistory(path)
    baseline = _run("a" * 40, run_id="baseline")
    candidate = _run("b" * 40, run_id="candidate")
    history.append(baseline)
    policy = ShadowGatePolicy(completion_rate_tolerance=0.5)

    record = history.append(candidate, baseline=baseline, policy=policy)

    # 自定义策略写入后必须能被重新加载（不再被默认策略误判为不一致）。
    reloaded = history.load()
    assert reloaded.records[-1].gate_allowed == record.gate_allowed
    assert reloaded.records[-1].gate_policy == policy.model_dump(mode="json")


def test_tampered_shadow_policy_snapshot_is_rejected(tmp_path):
    path = tmp_path / "shadow-history.json"
    history = JsonShadowHistory(path)
    baseline = _run("a" * 40, run_id="baseline")
    history.append(baseline)
    history.append(_run("b" * 40, run_id="candidate"), baseline=baseline)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][1]["gate_policy"]["completion_rate_tolerance"] = 1.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ShadowHistoryError, match="协议"):
        history.load()


def test_unsupported_shadow_schema_version_is_rejected(tmp_path):
    path = tmp_path / "shadow-history.json"
    history = JsonShadowHistory(path)
    history.append(_run("a" * 40, run_id="baseline"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "9.9.9"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ShadowHistoryError, match="协议"):
        history.load()


def test_legacy_record_without_policy_uses_default(tmp_path):
    path = tmp_path / "shadow-history.json"
    history = JsonShadowHistory(path)
    baseline = _run("a" * 40, run_id="baseline")
    candidate = _run("b" * 40, run_id="candidate")
    history.append(baseline)
    history.append(candidate, baseline=baseline)

    payload = json.loads(path.read_text(encoding="utf-8"))
    # 移除策略快照，模拟旧格式记录（应显式按默认策略复算）。
    payload["records"][1].pop("gate_policy", None)
    payload["records"][1].pop("gate_policy_hash", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = history.load()
    assert len(reloaded.records) == 2
