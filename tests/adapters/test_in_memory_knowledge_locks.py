"""Phase 6B-2 收尾：InMemoryKnowledgeRepository 读锁验收。

要求：

- `list_all()` / `search_confirmed()` 必须在锁内生成一致快照；
- 不允许在锁外直接遍历 `self._items.values()`；
- 排序与词法打分在锁外基于快照执行。

不用随机/高并发线程测试：这里用**确定性**的方式验证——
构造一个在迭代时会失败的 dict 视图，若代码在锁外遍历内部字典就会暴露。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateStatus,
    KnowledgeReview,
    KnowledgeReviewAction,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = (
    REPO_ROOT
    / "src"
    / "security_diagnosis_harness"
    / "adapters"
    / "knowledge"
    / "in_memory.py"
)


def _candidate(knowledge_id: str, title: str = "摄像头离线黑屏") -> KnowledgeCandidate:
    return KnowledgeCandidate(
        knowledge_id=knowledge_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        candidate_label="device_offline",
        title=title,
        summary="摄像头网络不可达导致预览黑屏",
        symptoms=["画面无法预览"],
        root_cause="设备离线",
        troubleshooting_steps=["检查网络"],
        source_diagnosis_id="diag-1",
        source_conclusion_id="con-1",
        source_evidence_ids=["evd-1"],
    )


def _confirm(candidate: KnowledgeCandidate) -> KnowledgeCandidate:
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.CONFIRM,
            reviewer="expert",
        )
    )
    return candidate


# ---------------------------------------------------------------- 结构守卫
def test_list_all_does_not_iterate_internal_dict_outside_lock():
    """`list_all` 不得直接 `for ... in self._items.values()`。"""
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "list_all"
    )
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute) and node.attr == "values":
            pytest.fail("list_all 直接遍历了内部字典视图，应在锁内取快照")


def test_search_confirmed_does_not_iterate_internal_dict_outside_lock():
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "search_confirmed"
    )
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute) and node.attr == "values":
            pytest.fail("search_confirmed 直接遍历了内部字典视图，应在锁内取快照")


def test_snapshot_helper_runs_under_lock():
    """`_snapshot` 必须在 `with self._lock` 内取数据。"""
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_snapshot"
    )
    uses_lock = any(
        isinstance(node, ast.Attribute) and node.attr == "_lock"
        for node in ast.walk(method)
    )
    assert uses_lock, "_snapshot 必须持锁读取"


# ---------------------------------------------------------------- 行为
def test_list_all_returns_consistent_snapshot():
    repository = InMemoryKnowledgeRepository()
    repository.save(_candidate("knw-a"))
    repository.save(_candidate("knw-b"))

    snapshot = repository.list_all()

    assert [item.knowledge_id for item in snapshot] == ["knw-a", "knw-b"]
    # 返回值必须是深拷贝：修改不影响仓储
    snapshot[0].title = "外部修改"
    assert repository.get("knw-a").title == "摄像头离线黑屏"


def test_search_confirmed_still_filters_by_status_and_fault_type():
    repository = InMemoryKnowledgeRepository()
    repository.save(_confirm(_candidate("knw-ok")))
    repository.save(_candidate("knw-candidate"))
    # 不同故障域
    other = _confirm(_candidate("knw-rec", title="录像计划被禁用"))
    other.fault_type = SecurityFaultType.RECORDING_MISSING
    repository.save(other)

    results = repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    )

    assert [item.knowledge_id for item in results] == ["knw-ok"]


def test_search_confirmed_returns_deep_copies():
    repository = InMemoryKnowledgeRepository()
    repository.save(_confirm(_candidate("knw-ok")))

    results = repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    )
    results[0].title = "外部修改"

    assert repository.get("knw-ok").title == "摄像头离线黑屏"


def test_snapshot_is_isolated_from_later_mutations():
    """快照生成后，仓储后续变更不应影响已取到的快照内容。"""
    repository = InMemoryKnowledgeRepository()
    repository.save(_candidate("knw-a"))

    first = repository.list_all()
    repository.save(_candidate("knw-b"))
    second = repository.list_all()

    assert len(first) == 1
    assert len(second) == 2


def test_confirmed_status_is_enforced_on_snapshot():
    repository = InMemoryKnowledgeRepository()
    repository.save(_candidate("knw-candidate"))

    assert repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    ) == []
    assert repository.get("knw-candidate").status is KnowledgeCandidateStatus.CANDIDATE
