from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.evaluation import (
    CodeBasedGrader,
    DatasetCase,
    DatasetRegistry,
    DatasetSplit,
    EvaluationHistoryError,
    EvaluationOutput,
    EvaluationRun,
    EvidenceTrace,
    FileHistoryHeadStore,
    InMemoryHistoryHeadStore,
    JsonEvaluationHistory,
    RunIdentity,
    ToolCallTrace,
    TrendComparabilityError,
    write_evaluation_trend_report,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "datasets/security-diagnosis/1.0.0"
HISTORY_INTEGRITY_KEY = b"history-test-integrity-key-32-bytes-minimum"
HISTORY_HEAD_STORE = InMemoryHistoryHeadStore()


def _history(path: Path) -> JsonEvaluationHistory:
    return JsonEvaluationHistory(
        path,
        integrity_key=HISTORY_INTEGRITY_KEY,
        head_store=HISTORY_HEAD_STORE,
    )


@pytest.fixture(scope="module")
def cases() -> tuple[DatasetCase, ...]:
    return DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)


def _output(case: DatasetCase, *, wrong: bool = False, p0: bool = False) -> EvaluationOutput:
    evidence = tuple(
        EvidenceTrace(evidence_id=f"{case.case_id}-{index}", evidence_type=evidence_type)
        for index, evidence_type in enumerate(case.required_evidence_types)
    )
    return EvaluationOutput(
        case_id=case.case_id,
        diagnosis_id=case.case_id,
        completed=True,
        candidate_label="wrong" if wrong else case.expected_candidate,
        conclusion_fault_type=case.fault_type,
        final_status=(
            SecurityDiagnosisStatus.CONFIRMED
            if p0
            else SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
        ),
        cited_evidence_ids=tuple(item.evidence_id for item in evidence),
        rounds=2,
        tool_calls=tuple(ToolCallTrace(tool_name=name, ok=True) for name in case.expected_tools),
        evidence=evidence,
    )


def _run(
    cases: tuple[DatasetCase, ...],
    commit: str,
    *,
    wrong: bool = False,
    p0: bool = False,
    **identity_changes,
) -> EvaluationRun:
    identity = RunIdentity(
        code_commit=commit,
        dataset_name="security-diagnosis",
        dataset_version="1.0.0",
        split=DatasetSplit.DEV,
        runner_name="phase8c-fixed",
        model_name="fake-llm",
        model_parameters={"temperature": 0},
        prompt_hash="1" * 64,
        configuration_hash="2" * 64,
        environment={"python": "3.12"},
    ).model_copy(update=identity_changes)
    return EvaluationRun(
        run_id=f"run-{commit[:7]}",
        created_at=datetime(2026, 9, 13, tzinfo=UTC) + timedelta(seconds=len(commit)),
        identity=identity,
        grade=CodeBasedGrader().grade_suite(
            cases, tuple(_output(case, wrong=wrong, p0=p0) for case in cases)
        ),
    )


def _expected(cases) -> tuple[DatasetCase, ...]:
    """受控 expected 锚：来自 DatasetCase，而非评分产物。"""
    return tuple(cases)


def test_first_run_is_persisted_as_baseline_summary(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    record = history.append(_run(cases, "a" * 40))
    assert record.baseline_run_id is None
    assert record.gate_allowed is None
    assert history.load().records == (record,)


def test_history_json_excludes_cases_findings_traces_and_environment_values(tmp_path, cases):
    path = tmp_path / "history.json"
    _history(path).append(_run(cases, "a" * 40))
    raw = path.read_text(encoding="utf-8")
    for forbidden in ('"cases"', '"findings"', '"tool_calls"', '"evidence"', '"grade"'):
        assert forbidden not in raw
    assert '"python": "3.12"' not in raw
    assert '"environment_hash"' in raw


def test_second_run_requires_persisted_matching_baseline(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    history.append(baseline)
    with pytest.raises(EvaluationHistoryError, match="必须提供"):
        history.append(_run(cases, "b" * 40))
    unknown = _run(cases, "c" * 40)
    with pytest.raises(EvaluationHistoryError, match="已入历史"):
        history.append(_run(cases, "b" * 40), baseline=unknown)


def test_baseline_run_id_with_changed_content_hash_is_rejected(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    history.append(baseline)
    changed = baseline.model_copy(
        update={
            "grade": CodeBasedGrader().grade_suite(
                cases, tuple(_output(case, wrong=True) for case in cases)
            )
        }
    )
    with pytest.raises(EvaluationHistoryError, match="哈希一致"):
        history.append(_run(cases, "b" * 40), baseline=changed)


def test_duplicate_run_id_is_rejected(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    run = _run(cases, "a" * 40)
    history.append(run)
    with pytest.raises(EvaluationHistoryError, match="已存在"):
        history.append(run)


@pytest.mark.parametrize(
    "changes",
    [
        {"dataset_version": "1.1.0"},
        {"model_name": "another-model"},
        {"prompt_hash": "3" * 64},
        {"configuration_hash": "4" * 64},
        {"model_parameters": {"temperature": 0.1}},
        {"environment": {"python": "3.13"}},
    ],
)
def test_incomparable_runs_never_enter_trend(tmp_path, cases, changes):
    history = _history(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    history.append(baseline)
    candidate = _run(cases, "b" * 40, **changes)
    with pytest.raises(TrendComparabilityError, match="变量") as excinfo:
        history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    assert excinfo.value.changed_fields
    assert len(history.load().records) == 1


def test_regression_reuses_phase7_gate_and_is_recorded(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40, wrong=True)
    history.append(baseline)
    record = history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    assert record.gate_allowed is False
    assert any("candidate_accuracy" in reason for reason in record.blocking_reasons)
    assert history.trend().blocked_run_ids == (candidate.run_id,)


def test_p0_is_recomputed_from_cases_and_blocks_trend_run(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40, p0=True)
    tampered_metrics = candidate.grade.metrics.model_copy(update={"p0_failure_count": 0})
    candidate = candidate.model_copy(
        update={"grade": candidate.grade.model_copy(update={"metrics": tampered_metrics})}
    )
    history.append(baseline)
    record = history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    assert record.blocked_by_p0 is True
    assert record.summary.deterministic_p0_count == len(cases)
    assert record.summary.metrics.p0_failure_count == len(cases)


def test_trend_contains_only_core_aggregate_metrics(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    report = history.trend()
    assert len(report.points) == 2
    assert set(report.points[0].metrics) == {
        "task_completion_rate", "candidate_accuracy", "candidate_macro_f1",
        "citation_compliance", "tool_recall", "evidence_coverage",
    }


def test_trend_report_is_atomic_and_contains_lineage(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    json_path, markdown_path = write_evaluation_trend_report(history.trend(), tmp_path)
    assert baseline.run_id in markdown_path.read_text(encoding="utf-8")
    assert candidate.run_id in json_path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_invalid_or_mixed_history_is_rejected(tmp_path, cases):
    path = tmp_path / "history.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(EvaluationHistoryError, match="协议"):
        _history(path).load()

    history = _history(path)
    first = _run(cases, "a" * 40)
    path.unlink()
    history.append(first)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][0]["summary"]["comparison_fingerprint"] = "f" * 64
    data["records"].append(data["records"][0] | {
        "summary": data["records"][0]["summary"] | {
            "run_id": "tampered-run", "comparison_fingerprint": "e" * 64
        }
    })
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationHistoryError, match="认证失败"):
        history.trend()


def _two_record_history(tmp_path, cases) -> tuple[JsonEvaluationHistory, object]:
    path = tmp_path / "history.json"
    history = _history(path)
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    return history, path


def test_load_rejects_duplicate_run_id(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    history.append(_run(cases, "a" * 40))
    data = json.loads(history._path.read_text(encoding="utf-8"))
    data["records"].append(data["records"][0])
    history._path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="文档认证失败"):
        history.load()


def test_load_rejects_future_baseline(tmp_path, cases):
    history, path = _two_record_history(tmp_path, cases)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"] = list(reversed(data["records"]))
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="文档认证失败"):
        history.load()


def test_load_rejects_tampered_gate_conclusion(tmp_path, cases):
    history, path = _two_record_history(tmp_path, cases)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][1]["gate_allowed"] = not data["records"][1]["gate_allowed"]
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="认证失败"):
        history.load()


def test_load_rejects_tampered_policy_snapshot(tmp_path, cases):
    history, path = _two_record_history(tmp_path, cases)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][1]["gate_policy"]["tool_recall_tolerance"] = 1.0
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="认证失败"):
        history.load()


def test_load_rejects_unsupported_schema_version(tmp_path, cases):
    history = _history(tmp_path / "history.json")
    history.append(_run(cases, "a" * 40))
    data = json.loads(history._path.read_text(encoding="utf-8"))
    data["schema_version"] = "5.0.0"
    history._path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="schema_version"):
        history.load()


def test_load_rejects_tampered_summary_metrics(tmp_path, cases):
    history, path = _two_record_history(tmp_path, cases)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][1]["summary"]["metrics"]["candidate_accuracy"] = 0.123
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="认证失败"):
        history.load()


def test_load_recomputes_gate_from_valid_summary(tmp_path, cases):
    history, path = _two_record_history(tmp_path, cases)
    data = json.loads(path.read_text(encoding="utf-8"))
    record = data["records"][1]
    record["blocking_reasons"] = ["invented reason"]
    record["gate_allowed"] = False
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="认证失败"):
        history.load()


def test_load_rejects_history_tail_truncation(tmp_path, cases):
    history, path = _two_record_history(tmp_path, cases)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"].pop()
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="文档认证失败"):
        history.load()


def test_load_rejects_valid_old_snapshot_replay(tmp_path, cases):
    path = tmp_path / "history.json"
    history = _history(path)
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    old_authenticated_snapshot = path.read_bytes()
    history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    path.write_bytes(old_authenticated_snapshot)

    with pytest.raises(EvaluationHistoryError, match="旧快照回放"):
        history.load()


def test_model_copy_cannot_bypass_sensitive_identity_validation(tmp_path, cases):
    run = _run(cases, "a" * 40)
    unsafe_identity = run.identity.model_copy(
        update={"environment": {"api_key": "DO-NOT-PERSIST"}}
    )
    unsafe_run = run.model_copy(update={"identity": unsafe_identity})
    with pytest.raises(ValueError, match="敏感"):
        _history(tmp_path / "history.json").append(unsafe_run)
    assert not (tmp_path / "history.json").exists()


def test_loaded_summary_rejects_sensitive_model_parameters(tmp_path, cases):
    path = tmp_path / "history.json"
    history = _history(path)
    history.append(_run(cases, "a" * 40))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][0]["summary"]["model_parameters"] = {
        "api_key": "DO-NOT-LOAD"
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationHistoryError, match="协议"):
        history.load()


# ---------------------------------------------------------------- 3 可持久化 Head
def _file_history(path: Path, head_path: Path) -> JsonEvaluationHistory:
    """使用落盘 HeadStore 的历史实例；head 与文档放在不同目录。"""
    return JsonEvaluationHistory(
        path,
        integrity_key=HISTORY_INTEGRITY_KEY,
        head_store=FileHistoryHeadStore(head_path),
    )


def _heads_path(tmp_path: Path) -> Path:
    # Head 与历史文档分离存放，避免二者被一起回滚。
    return tmp_path / "trusted-heads" / "history-heads.json"


def test_file_head_store_survives_process_restart(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    baseline = _run(cases, "a" * 40)
    _file_history(path, head_path).append(baseline)

    # 模拟进程重启：全新实例 + 全新 HeadStore 读取同一份落盘状态。
    restarted = _file_history(path, head_path)
    document = restarted.load()
    assert [record.summary.run_id for record in document.records] == [baseline.run_id]
    assert restarted.trend().points[0].run_id == baseline.run_id


def test_file_head_store_does_not_persist_integrity_key(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    _file_history(path, head_path).append(_run(cases, "a" * 40))

    secret = HISTORY_INTEGRITY_KEY.decode("utf-8")
    for artifact in (path, head_path):
        assert artifact.exists()
        assert secret not in artifact.read_text(encoding="utf-8")
    # Head 文件只保存不透明认证标签，不保存任何密钥材料。
    heads = json.loads(head_path.read_text(encoding="utf-8"))
    assert all(len(tag) == 64 and set(tag) <= set("0123456789abcdef") for tag in heads.values())


def test_file_head_store_rejects_missing_head(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    _file_history(path, head_path).append(_run(cases, "a" * 40))
    head_path.unlink()

    with pytest.raises(EvaluationHistoryError, match="缺少外部可信 Head"):
        _file_history(path, head_path).load()


def test_file_head_store_rejects_tampered_head(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    _file_history(path, head_path).append(_run(cases, "a" * 40))
    heads = json.loads(head_path.read_text(encoding="utf-8"))
    heads[next(iter(heads))] = "f" * 64
    head_path.write_text(json.dumps(heads), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="旧快照回放"):
        _file_history(path, head_path).load()


def test_file_head_store_rejects_rollback_across_restart(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    history = _file_history(path, head_path)
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    old_snapshot = path.read_bytes()
    history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    path.write_bytes(old_snapshot)

    with pytest.raises(EvaluationHistoryError, match="旧快照回放"):
        _file_history(path, head_path).load()


def test_file_head_store_rejects_truncation_across_restart(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    history = _file_history(path, head_path)
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    history.append(candidate, baseline=baseline, dataset_cases=_expected(cases))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"].pop()
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationHistoryError, match="文档认证失败"):
        _file_history(path, head_path).load()


# ---------------------------------------------------------------- 4 并发与恢复
def _published_document(tmp_path: Path, cases, *, name: str) -> dict:
    """用独立副本生成"下一次发布"的文档内容，供崩溃恢复场景注入。"""
    scratch_path = tmp_path / f"{name}.json"
    scratch = _file_history(scratch_path, tmp_path / f"{name}-heads.json")
    baseline = _run(cases, "a" * 40)
    scratch.append(baseline)
    scratch.append(
        _run(cases, "b" * 40), baseline=baseline, dataset_cases=_expected(cases)
    )
    return json.loads(scratch_path.read_text(encoding="utf-8"))


def test_two_instances_can_append_concurrently_without_lost_update(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    first = _file_history(path, head_path)
    second = _file_history(path, head_path)
    baseline = _run(cases, "a" * 40)
    candidate_b = _run(cases, "b" * 40)
    candidate_c = _run(cases, "c" * 40)
    first.append(baseline)

    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def worker(history: JsonEvaluationHistory, candidate: EvaluationRun) -> None:
        try:
            barrier.wait(timeout=5)
            history.append(
                candidate, baseline=baseline, dataset_cases=_expected(cases)
            )
        except BaseException as exc:  # noqa: BLE001 - 记录后统一断言
            failures.append(exc)

    threads = [
        threading.Thread(target=worker, args=(first, candidate_b)),
        threading.Thread(target=worker, args=(second, candidate_c)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    document = first.load()
    run_ids = [record.summary.run_id for record in document.records]
    # 两个并发 append 都必须存在，不丢更新。
    assert run_ids[0] == baseline.run_id
    assert set(run_ids) == {baseline.run_id, candidate_b.run_id, candidate_c.run_id}
    # baseline 血缘合法：两条 candidate 都指向同一个已入历史的 baseline。
    for record in document.records[1:]:
        assert record.baseline_run_id == baseline.run_id
        assert record.gate_allowed is True
    # 不残留 staging / pending 文件。
    assert not list(tmp_path.glob("*.pending"))
    assert not list(tmp_path.glob("*.tmp"))


def test_publish_crash_after_head_commit_is_recovered_from_pending(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    history = _file_history(path, head_path)
    baseline = _run(cases, "a" * 40)
    history.append(baseline)

    published = _published_document(tmp_path, cases, name="scratch-head-commit")
    pending = tmp_path / "history.json.pending"
    pending.write_text(json.dumps(published), encoding="utf-8")
    # 模拟崩溃点：pending 已落盘、head 已推进，但正式文档尚未替换。
    FileHistoryHeadStore(head_path).save_head(
        str(path.resolve()), published["document_auth_tag"]
    )

    recovered = _file_history(path, head_path).load()

    assert [record.summary.run_id for record in recovered.records] == [
        baseline.run_id,
        _run(cases, "b" * 40).run_id,
    ]
    assert not pending.exists()


def test_stale_pending_before_head_commit_is_discarded(tmp_path, cases):
    path = tmp_path / "history.json"
    head_path = _heads_path(tmp_path)
    history = _file_history(path, head_path)
    baseline = _run(cases, "a" * 40)
    history.append(baseline)

    published = _published_document(tmp_path, cases, name="scratch-stale")
    pending = tmp_path / "history.json.pending"
    pending.write_text(json.dumps(published), encoding="utf-8")
    # head 未推进：残留 pending 必须被丢弃，正式文档仍是旧的一致快照。

    document = _file_history(path, head_path).load()

    assert [record.summary.run_id for record in document.records] == [baseline.run_id]
    assert not pending.exists()

