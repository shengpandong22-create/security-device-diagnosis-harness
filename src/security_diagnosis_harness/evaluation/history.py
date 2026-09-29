"""Phase 8C safe evaluation history, comparable trends, and gate lineage."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock, RLock
from types import TracebackType
from typing import Any, BinaryIO, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.redaction import redact_mapping
from security_diagnosis_harness.evaluation.dataset import DatasetCase, DatasetSplit
from security_diagnosis_harness.evaluation.gate import (
    CORE_METRICS,
    AuthenticatedDatasetCases,
    AuthenticatedEvaluationRun,
    ComparisonConfigurationError,
    EvaluationRun,
    GatePolicy,
    RunIdentity,
    compare_runs,
)
from security_diagnosis_harness.evaluation.grader import SuiteMetrics


class EvaluationHistoryError(ValueError):
    """History cannot accept or read the requested evaluation run."""


class HistoryHeadStore(Protocol):
    """Trusted storage outside the replayable history document."""

    def load_head(self, history_id: str) -> str | None: ...

    def save_head(self, history_id: str, auth_tag: str) -> None: ...


class InMemoryHistoryHeadStore:
    """Process-local implementation for tests and isolated offline runs.

    注意：进程重启后 Head 会丢失，正式 / 跨重启场景必须使用
    :class:`FileHistoryHeadStore`。
    """

    def __init__(self) -> None:
        self._heads: dict[str, str] = {}
        self._lock = RLock()

    def load_head(self, history_id: str) -> str | None:
        with self._lock:
            return self._heads.get(history_id)

    def save_head(self, history_id: str, auth_tag: str) -> None:
        with self._lock:
            self._heads[history_id] = auth_tag


class FileHistoryHeadStore:
    """单机可持久化的可信 Head 存储。

    一个文件可以同时保存**多段**历史的 Head（``{"<history_id>": "<auth_tag>"}``），
    因此必须假设存在"多个历史文件共享同一个 Head 文件"的装配方式。

    信任边界（单机、显式声明）：

    - 只回答"某段评测历史**曾经**提交过哪个文档认证标签"，用于识别历史文档被
      回滚、截尾或替换成旧快照；它不校验历史内容本身（那由文档 / 记录 HMAC 负责）；
    - 只保护**同机**信任域：能改写 Head 文件的攻击者同样能改写历史文档，因此这是
      单机文件系统信任边界，不是分布式、多副本或抗内部篡改保证；
    - **不保存完整性密钥**：文件里只有不透明的 HMAC 认证标签（hex 摘要），密钥
      由调用方在内存中注入、绝不落盘，也不会写进锁文件或 staging 文件；
    - 建议 Head 文件与历史文档**分离存放**（不同目录或不同介质），避免二者被
      一起回滚。

    并发与崩溃：

    - ``load_head`` 与 ``save_head`` 的文件访问都在**同一把 Head 自己的锁**内；
      ``save_head`` 的完整 read → modify → stage → replace 周期在该锁内完成，因此
      同进程多实例、同机多进程、以及多个不同历史文件共享同一个 Head 文件时都不会
      丢更新；
    - 锁文件路径推导：``<head 文件路径> + ".head-lock"``。刻意与历史文档的
      ``.lock`` 区分，避免 Head 锁与历史锁退化成同一个文件；
    - 写入使用**唯一的** staging 文件名（``<head>.<pid>.<uuid>.staging``）+ 原子
      替换，因此不存在固定临时文件名冲突，Head 文件本身永远不会是半个 JSON；
    - 锁顺序恒为"历史锁 → Head 锁"（History 在持锁时调用本 Store），本 Store 从不
      反向获取历史锁，因此不存在锁循环。
    """

    #: Head 锁文件后缀；与历史文档的 ``.lock`` 刻意区分。
    LOCK_SUFFIX = ".head-lock"

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        #: 锁文件路径的唯一推导方式：``<head>.head-lock``。
        self._lock_path = self._path.with_suffix(self._path.suffix + self.LOCK_SUFFIX)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def load_head(self, history_id: str) -> str | None:
        with _HistoryFileLock(self._lock_path):
            return self._read().get(history_id)

    def save_head(self, history_id: str, auth_tag: str) -> None:
        # 完整 read → modify → stage → replace 周期都在同一把 Head 锁内。
        with _HistoryFileLock(self._lock_path):
            heads = self._read()
            if heads.get(history_id) == auth_tag:
                return
            heads[history_id] = auth_tag
            self._write(heads)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EvaluationHistoryError("可信 Head 文件无法解析") from exc
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw.items()
        ):
            raise EvaluationHistoryError("可信 Head 文件格式非法")
        return raw

    def _write(self, heads: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 唯一 staging 名：即使有人在未持锁的情况下调用，也不会与其它写者冲突。
        staging = self._path.with_name(
            f"{self._path.name}.{os.getpid()}.{uuid4().hex}.staging"
        )
        try:
            staging.write_text(
                json.dumps(heads, ensure_ascii=True, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            staging.replace(self._path)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise


# --------------------------------------------------------------- 同机排它锁
# 同一路径共享同一个进程内 RLock：模块级注册表保证"同进程多实例"也串行化，
# 不依赖操作系统的锁在单进程内的可见性。
#
# 本节的锁既服务历史文档（``<history>.lock``），也服务 Head 文件
# （``<head>.head-lock``，见 FileHistoryHeadStore，其类定义在本节之前，名称在
# 调用时解析）。两者互不嵌套反向获取，锁顺序恒为"历史锁 → Head 锁"。
_THREAD_LOCKS: dict[str, RLock] = {}
_THREAD_LOCKS_GUARD = Lock()


def _shared_thread_lock(key: str) -> RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, RLock())


def _acquire_os_lock(handle: BinaryIO) -> None:
    """获取跨进程的排它锁；阻塞直到成功。"""
    if os.name == "nt":  # pragma: no cover - 平台相关分支
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:  # pragma: no cover - 平台相关分支
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release_os_lock(handle: BinaryIO) -> None:
    if os.name == "nt":  # pragma: no cover - 平台相关分支
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - 平台相关分支
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _HistoryFileLock:
    """单机排它锁：串行化历史文档的 load-modify-write 周期。

    同时覆盖**同进程多实例**（共享 RLock）与**同机多进程**（OS 文件锁）。
    信任边界与 Head 一致：只保证同机互斥，不保护跨主机共享目录。
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._thread_lock = _shared_thread_lock(str(self._path.resolve()))
        self._handle: BinaryIO | None = None

    def __enter__(self) -> _HistoryFileLock:
        self._thread_lock.acquire()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a+b")
            _acquire_os_lock(self._handle)
        except BaseException:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._thread_lock.release()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        try:
            if self._handle is not None:
                _release_os_lock(self._handle)
        finally:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._thread_lock.release()
        return False


class TrendComparabilityError(ComparisonConfigurationError):
    """A run changes controlled variables and cannot enter this trend."""

    def __init__(self, changed_fields: tuple[str, ...]) -> None:
        self.changed_fields = changed_fields
        super().__init__(f"不可比变量变化: {', '.join(changed_fields)}")


class EvaluationRunSummary(BaseModel):
    """Sanitized aggregate-only view; deliberately excludes cases and traces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    created_at: datetime
    code_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    dataset_name: str
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    split: DatasetSplit
    runner_name: str
    model_name: str
    model_parameters: dict[str, Any]
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: SuiteMetrics
    deterministic_p0_count: int = Field(ge=0)
    summary_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _reject_sensitive_metadata(self) -> EvaluationRunSummary:
        redacted, changed = redact_mapping(self.model_parameters)
        if changed or redacted != self.model_parameters:
            raise ValueError("model_parameters 不得包含敏感信息")
        return self

    @classmethod
    def from_run(cls, run: EvaluationRun) -> EvaluationRunSummary:
        # Revalidate at the persistence boundary to block model_copy bypasses.
        identity = RunIdentity.model_validate(run.identity.model_dump(mode="python"))
        p0_count = sum(case.p0_blocked for case in run.grade.cases)
        metrics = run.grade.metrics.model_copy(update={"p0_failure_count": p0_count})
        summary = cls(
            run_id=run.run_id,
            created_at=run.created_at,
            code_commit=identity.code_commit,
            dataset_name=identity.dataset_name,
            dataset_version=identity.dataset_version,
            split=identity.split,
            runner_name=identity.runner_name,
            model_name=identity.model_name,
            model_parameters=identity.model_parameters,
            prompt_hash=identity.prompt_hash,
            configuration_hash=identity.configuration_hash,
            environment_hash=sha256_text(canonical_json(identity.environment)),
            comparison_fingerprint="0" * 64,
            run_content_hash=run.content_hash(),
            metrics=metrics,
            deterministic_p0_count=p0_count,
            summary_content_hash="0" * 64,
        )
        summary = summary.model_copy(
            update={
                "comparison_fingerprint": sha256_text(
                    canonical_json(_controlled_summary_values(summary))
                )
            }
        )
        return summary.model_copy(update={"summary_content_hash": _summary_hash(summary)})


class EvaluationHistoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: EvaluationRunSummary
    baseline_run_id: str | None = None
    gate_allowed: bool | None = None
    blocked_by_p0: bool = False
    blocking_reasons: tuple[str, ...] = ()
    # 非敏感 GatePolicy 快照与其规范化哈希；用于加载期复验策略未被篡改。
    gate_policy: dict[str, Any] | None = None
    gate_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationHistoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "4.0.0"
    records: tuple[EvaluationHistoryRecord, ...] = ()
    document_auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")


#: 当前受支持的评测历史 schema 版本。其它版本明确拒绝（fail-closed），
#: 不做隐式兼容，避免"格式合法但语义不同"的历史被静默接受。
SUPPORTED_HISTORY_SCHEMA_VERSIONS: frozenset[str] = frozenset({"4.0.0"})


def _record_auth_tag(record: EvaluationHistoryRecord, integrity_key: bytes) -> str:
    payload = canonical_json(
        record.model_dump(mode="json", exclude={"record_auth_tag"})
    ).encode()
    return hmac.new(integrity_key, payload, hashlib.sha256).hexdigest()


def _document_auth_tag(
    document: EvaluationHistoryDocument, integrity_key: bytes
) -> str:
    payload = canonical_json(
        document.model_dump(mode="json", exclude={"document_auth_tag"})
    ).encode()
    return hmac.new(integrity_key, payload, hashlib.sha256).hexdigest()


def _summary_hash(value: EvaluationRunSummary | dict[str, Any]) -> str:
    payload = (
        value.model_dump(mode="json", exclude={"summary_content_hash"})
        if isinstance(value, EvaluationRunSummary)
        else value
    )
    return sha256_text(canonical_json(payload))


def _controlled_summary_values(summary: EvaluationRunSummary) -> dict[str, Any]:
    return {
        "dataset_name": summary.dataset_name,
        "dataset_version": summary.dataset_version,
        "split": summary.split.value,
        "runner_name": summary.runner_name,
        "model_name": summary.model_name,
        "model_parameters": summary.model_parameters,
        "prompt_hash": summary.prompt_hash,
        "configuration_hash": summary.configuration_hash,
        "environment_hash": summary.environment_hash,
    }


def _recompute_summary_gate(
    baseline: EvaluationRunSummary,
    candidate: EvaluationRunSummary,
    policy: GatePolicy,
) -> tuple[bool, bool, tuple[str, ...]]:
    blocked_by_p0 = candidate.deterministic_p0_count > 0
    reasons = (
        [f"Candidate 存在 {candidate.deterministic_p0_count} 个 P0 失败"]
        if blocked_by_p0
        else []
    )
    for metric, tolerance_name in CORE_METRICS:
        before = float(getattr(baseline.metrics, metric))
        after = float(getattr(candidate.metrics, metric))
        if after - before < -float(getattr(policy, tolerance_name)):
            reasons.append(f"核心指标退化: {metric}")
    return not reasons, blocked_by_p0, tuple(reasons)


def _validate_document(
    document: EvaluationHistoryDocument, integrity_key: bytes
) -> EvaluationHistoryDocument:
    """复验历史文档的结构不变量；拒绝被篡改或顺序错误的历史。"""
    if document.schema_version not in SUPPORTED_HISTORY_SCHEMA_VERSIONS:
        raise EvaluationHistoryError("不支持的评测历史 schema_version")
    if not hmac.compare_digest(
        document.document_auth_tag, _document_auth_tag(document, integrity_key)
    ):
        raise EvaluationHistoryError("评测历史文档认证失败")
    seen: set[str] = set()
    summaries: dict[str, EvaluationRunSummary] = {}
    for record in document.records:
        expected_auth_tag = _record_auth_tag(record, integrity_key)
        if not hmac.compare_digest(record.record_auth_tag, expected_auth_tag):
            raise EvaluationHistoryError("评测历史记录认证失败")
        summary = record.summary
        if summary.comparison_fingerprint != sha256_text(
            canonical_json(_controlled_summary_values(summary))
        ):
            raise EvaluationHistoryError("comparison_fingerprint 与运行身份不一致")
        if summary.summary_content_hash != _summary_hash(summary):
            raise EvaluationHistoryError("summary_content_hash 与历史摘要不一致")
        if summary.run_id in seen:
            raise EvaluationHistoryError(f"评测历史存在重复 run_id: {summary.run_id}")
        baseline_id = record.baseline_run_id
        if baseline_id is None:
            if record.gate_allowed is not None:
                raise EvaluationHistoryError("基线记录不得携带 Gate 结论")
            if record.blocked_by_p0 or record.blocking_reasons:
                raise EvaluationHistoryError("基线记录不得携带阻塞原因")
        else:
            if baseline_id not in seen:
                raise EvaluationHistoryError("Baseline 必须指向此前已入历史的记录")
            if record.gate_allowed is None:
                raise EvaluationHistoryError("非基线记录必须携带 Gate 结论")
            if record.gate_allowed != (not record.blocking_reasons):
                raise EvaluationHistoryError("Gate 结论与阻塞原因不一致")
            if record.blocked_by_p0 and record.gate_allowed:
                raise EvaluationHistoryError("P0 阻塞记录的 Gate 结论不能为允许")
        if record.gate_policy_hash is not None:
            if record.gate_policy is None:
                raise EvaluationHistoryError("Gate 策略哈希必须绑定策略快照")
            if sha256_text(canonical_json(record.gate_policy)) != record.gate_policy_hash:
                raise EvaluationHistoryError("Gate 策略快照与哈希不一致")
        if baseline_id is not None:
            if record.gate_policy is None:
                raise EvaluationHistoryError("非基线记录必须保存 Gate 策略")
            expected = _recompute_summary_gate(
                summaries[baseline_id], summary, GatePolicy.model_validate(record.gate_policy)
            )
            actual = (record.gate_allowed, record.blocked_by_p0, record.blocking_reasons)
            if actual != expected:
                raise EvaluationHistoryError("Gate 结论与历史摘要重算结果不一致")
        seen.add(summary.run_id)
        summaries[summary.run_id] = summary
    return document


class TrendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    code_commit: str
    created_at: datetime
    baseline_run_id: str | None
    gate_allowed: bool | None
    metrics: dict[str, float]
    deterministic_p0_count: int


class EvaluationTrendReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_fingerprint: str
    dataset_name: str
    dataset_version: str
    model_name: str
    prompt_hash: str
    configuration_hash: str
    points: tuple[TrendPoint, ...]
    blocked_run_ids: tuple[str, ...]

    def to_markdown(self) -> str:
        lines = [
            "# Phase 8C 评测趋势报告", "",
            f"- Dataset: `{self.dataset_name}@{self.dataset_version}`",
            f"- Model: `{self.model_name}`", f"- Prompt hash: `{self.prompt_hash}`",
            f"- Configuration hash: `{self.configuration_hash}`",
            f"- 可比运行数: `{len(self.points)}`", "", "## 趋势", "",
            "| Run | Commit | Baseline | Gate | P0 | Candidate accuracy | Tool recall |",
            "|---|---|---|---|---:|---:|---:|",
        ]
        for point in self.points:
            gate = "BASELINE" if point.gate_allowed is None else (
                "PASS" if point.gate_allowed else "BLOCKED"
            )
            lines.append(
                f"| `{point.run_id}` | `{point.code_commit}` | "
                f"`{point.baseline_run_id or '-'}` | `{gate}` | "
                f"{point.deterministic_p0_count} | "
                f"{point.metrics['candidate_accuracy']:.4f} | "
                f"{point.metrics['tool_recall']:.4f} |"
            )
        return "\n".join(lines) + "\n"


class JsonEvaluationHistory:
    """Atomic local history repository containing summaries, never full runs.

    Concurrency and crash recovery:

    - the whole ``load → stage → publish → save head`` cycle runs under one
      same-machine exclusive lock (per path), so two instances (threads or
      processes) can no longer overwrite each other;
    - staging uses a deterministic ``.pending`` slot guarded by that lock, so the
      old "everyone writes the same ``.tmp``" race is gone;
    - publish is ordered ``stage → save head → atomic replace``: a crash after the
      head advances but before the document is replaced is finished on the next
      ``load()`` from the pending slot; a crash before the head advances leaves a
      stale pending slot that is discarded.
    """

    def __init__(
        self,
        path: Path,
        *,
        integrity_key: bytes,
        head_store: HistoryHeadStore,
    ) -> None:
        if len(integrity_key) < 32:
            raise ValueError("评测历史完整性密钥至少需要 32 字节")
        self._path = Path(path)
        self._integrity_key = bytes(integrity_key)
        self._head_store = head_store
        self._history_id = str(self._path.resolve())
        self._lock = _HistoryFileLock(self._path.with_suffix(self._path.suffix + ".lock"))
        #: 崩溃恢复槽位：先于 head 更新写入，可能在任何写入阶段留下。
        self._pending_path = self._path.with_suffix(self._path.suffix + ".pending")

    def _empty_document(self) -> EvaluationHistoryDocument:
        document = EvaluationHistoryDocument(document_auth_tag="0" * 64)
        return document.model_copy(
            update={
                "document_auth_tag": _document_auth_tag(
                    document, self._integrity_key
                )
            }
        )

    def load(self) -> EvaluationHistoryDocument:
        with self._lock:
            return self._load_locked()

    def _load_locked(self) -> EvaluationHistoryDocument:
        trusted_head = self._head_store.load_head(self._history_id)
        self._recover_pending(trusted_head)
        if not self._path.exists():
            document = self._empty_document()
            if trusted_head is not None and trusted_head != document.document_auth_tag:
                raise EvaluationHistoryError("评测历史文件缺失或已回滚")
            return document
        try:
            document = EvaluationHistoryDocument.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise EvaluationHistoryError("评测历史文件无法通过协议校验") from exc
        document = _validate_document(document, self._integrity_key)
        if trusted_head is None:
            raise EvaluationHistoryError("评测历史缺少外部可信 Head")
        if not hmac.compare_digest(trusted_head, document.document_auth_tag):
            raise EvaluationHistoryError("评测历史检测到旧快照回放")
        return document

    def _recover_pending(self, trusted_head: str | None) -> None:
        """完成或丢弃被中断的发布；绝不接受回滚到旧快照。"""
        if not self._pending_path.exists():
            return
        if trusted_head is not None:
            try:
                candidate = EvaluationHistoryDocument.model_validate_json(
                    self._pending_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                candidate = None
            if (
                candidate is not None
                and hmac.compare_digest(candidate.document_auth_tag, trusted_head)
            ):
                # 崩溃发生在「已提交新 head」与「替换正式文档」之间：完成发布。
                self._pending_path.replace(self._path)
                return
        # head 未推进（或缺失）→ pending 尚未提交，丢弃即可（正式文档仍是旧的一致快照）。
        self._pending_path.unlink(missing_ok=True)

    def append(
        self,
        run: EvaluationRun,
        *,
        baseline: EvaluationRun | None = None,
        policy: GatePolicy | None = None,
        dataset_cases: tuple[DatasetCase, ...] | None = None,
    ) -> EvaluationHistoryRecord:
        with self._lock:
            record = self._append_locked(
                run, baseline=baseline, policy=policy, dataset_cases=dataset_cases
            )
        return record

    def _append_locked(
        self,
        run: EvaluationRun,
        *,
        baseline: EvaluationRun | None,
        policy: GatePolicy | None,
        dataset_cases: tuple[DatasetCase, ...] | None,
    ) -> EvaluationHistoryRecord:
        document = self._load_locked()
        summaries = {record.summary.run_id: record.summary for record in document.records}
        if run.run_id in summaries:
            raise EvaluationHistoryError(f"评测运行已存在: {run.run_id}")

        if document.records and baseline is None:
            raise EvaluationHistoryError("非首个趋势运行必须提供已入历史的 Baseline")
        if not document.records and baseline is not None:
            raise EvaluationHistoryError("首个历史记录不能声明 Baseline")

        if baseline is None:
            record = EvaluationHistoryRecord(
                summary=EvaluationRunSummary.from_run(run),
                record_auth_tag="0" * 64,
            )
        else:
            persisted = summaries.get(baseline.run_id)
            if persisted is None or persisted.run_content_hash != baseline.content_hash():
                raise EvaluationHistoryError("Baseline 必须已入历史且内容哈希一致")
            changes = comparison_changes(baseline, run)
            if changes:
                raise TrendComparabilityError(changes)
            gate = compare_runs(
                AuthenticatedEvaluationRun.issue(baseline, self._integrity_key),
                AuthenticatedEvaluationRun.issue(run, self._integrity_key),
                policy,
                dataset_cases=(
                    AuthenticatedDatasetCases.issue(
                        run.identity.dataset_name,
                        dataset_cases,
                        self._integrity_key,
                    )
                    if dataset_cases is not None
                    else None
                ),
                integrity_key=self._integrity_key,
            )
            policy_snapshot = (policy or GatePolicy()).model_dump(mode="json")
            record = EvaluationHistoryRecord(
                summary=EvaluationRunSummary.from_run(run),
                baseline_run_id=baseline.run_id,
                gate_allowed=gate.allowed,
                blocked_by_p0=gate.blocked_by_p0,
                blocking_reasons=gate.blocking_reasons,
                gate_policy=policy_snapshot,
                gate_policy_hash=sha256_text(canonical_json(policy_snapshot)),
                record_auth_tag="0" * 64,
            )
        record = record.model_copy(
            update={"record_auth_tag": _record_auth_tag(record, self._integrity_key)}
        )
        updated = document.model_copy(
            update={"records": (*document.records, record), "document_auth_tag": "0" * 64}
        )
        updated = updated.model_copy(
            update={
                "document_auth_tag": _document_auth_tag(updated, self._integrity_key)
            }
        )
        self._write(updated)
        return record

    def trend(self) -> EvaluationTrendReport:
        document = self.load()
        if not document.records:
            raise EvaluationHistoryError("没有可生成趋势的评测运行")
        fingerprints = {item.summary.comparison_fingerprint for item in document.records}
        if len(fingerprints) != 1:
            raise EvaluationHistoryError("历史包含不可比运行，拒绝生成混合趋势")
        first = document.records[0].summary
        points = tuple(_trend_point(item) for item in document.records)
        return EvaluationTrendReport(
            comparison_fingerprint=first.comparison_fingerprint,
            dataset_name=first.dataset_name,
            dataset_version=first.dataset_version,
            model_name=first.model_name,
            prompt_hash=first.prompt_hash,
            configuration_hash=first.configuration_hash,
            points=points,
            blocked_run_ids=tuple(
                point.run_id for point in points if point.gate_allowed is False
            ),
        )

    def _write(self, document: EvaluationHistoryDocument) -> None:
        """发布新历史快照：stage → 提交 head → 原子替换（调用方必须持锁）。

        顺序刻意如此：pending 槽位先落盘，head 再推进，最后才替换正式文档。
        这样崩溃点有两种，且都能收敛到一致状态：

        - 在 head 推进前崩溃：head 仍是旧值、正式文档仍是旧的一致快照，
          残留 pending 会在下次 ``load()`` 被丢弃；
        - 在 head 推进后、替换前崩溃：head 已是新值、pending 内容与之一致，
          下次 ``load()`` 会用它完成发布。
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._pending_path.write_text(
            document.model_dump_json(indent=2), encoding="utf-8"
        )
        self._head_store.save_head(self._history_id, document.document_auth_tag)
        self._pending_path.replace(self._path)


def write_evaluation_trend_report(
    report: EvaluationTrendReport, output_directory: Path
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "phase8-evaluation-trend.json"
    markdown_path = output_directory / "phase8-evaluation-trend.md"
    _atomic_write(json_path, report.model_dump_json(indent=2))
    _atomic_write(markdown_path, report.to_markdown())
    return json_path, markdown_path


def comparison_changes(
    baseline: EvaluationRun, candidate: EvaluationRun
) -> tuple[str, ...]:
    """Name controlled variables that changed; facts and outputs are never inspected."""
    before = baseline.identity
    after = candidate.identity
    fields = (
        "dataset_name", "dataset_version", "split", "runner_name", "model_name",
        "model_parameters", "prompt_hash", "configuration_hash", "environment",
    )
    return tuple(name for name in fields if getattr(before, name) != getattr(after, name))


def _trend_point(record: EvaluationHistoryRecord) -> TrendPoint:
    summary = record.summary
    return TrendPoint(
        run_id=summary.run_id,
        code_commit=summary.code_commit,
        created_at=summary.created_at,
        baseline_run_id=record.baseline_run_id,
        gate_allowed=record.gate_allowed,
        metrics={name: float(getattr(summary.metrics, name)) for name, _ in CORE_METRICS},
        deterministic_p0_count=summary.deterministic_p0_count,
    )


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
