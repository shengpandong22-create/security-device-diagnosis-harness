"""Phase 6B-2 收尾：Application 层真实冲突不自动重试验收。

两个场景都通过 `ConflictInjectingDiagnosisRepository` 在 Application
最终 `repository.update(case)` **之前**注入一次竞争者的合法 CAS 更新，
从而制造真实 stale-copy 冲突。

- 场景 A（run_diagnosis）：Runner 恰好执行 1 次（不是 0 次），不重放；
- 场景 B（review_diagnosis）：HumanReview 不落库，赢家保留。
  断言异常**精确**为 `ConcurrentUpdateError`，而不是终态导致的
  `ReviewNotAllowed` / `InvalidStatusTransition`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from security_diagnosis_harness.application.diagnoses import (
    SecurityDiagnosisApplicationService,
)
from security_diagnosis_harness.application.errors import ConcurrentUpdateError
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction
from security_diagnosis_harness.runtime import build_runtime_container
from tests.persistence._conflict_repository import (
    ConflictInjectingDiagnosisRepository,
)

DEVICE_ID = "camera-3f-001"
REPORTER = "probe"
LOSING_COMMENT = "应该失败的审核"


def _build_service_with_conflict(tmp_path: Path, *, conflict_index: int, competitor):
    """返回 (service, wrapper, container, run_calls)。"""
    url = f"sqlite:///{(tmp_path / 'conflict.db').as_posix()}"
    settings = RuntimeSettings(repository_mode="sqlite", database_url=url)
    container = build_runtime_container(settings)

    wrapper = ConflictInjectingDiagnosisRepository(
        container.repository,
        conflict_on_update_index=conflict_index,
        competitor_mutator=competitor,
    )

    service = SecurityDiagnosisApplicationService(
        repository=wrapper,
        runner=container.runner,
        registry=container.registry,
        gateway=container.gateway,
        citation_policy=container.citation_policy,
        supported_fault_types=container.service.supported_fault_types,
    )

    run_calls = {"count": 0}
    original_run = container.runner.run

    def _counting_run(*args, **kwargs):
        run_calls["count"] += 1
        return original_run(*args, **kwargs)

    container.runner.run = _counting_run  # type: ignore[method-assign]

    return service, wrapper, container, run_calls


def _tag_reporter(case) -> None:
    case.reporter = "competitor-winner"


def _competitor_confirms(case) -> None:
    """竞争者抢先完成一次合法人工确认。"""
    case.apply_human_review(
        HumanReview(
            diagnosis_id=case.diagnosis_id,
            action=HumanReviewAction.CONFIRM,
            reviewer="competitor",
            comment="竞争者先确认",
        )
    )


# ---------------------------------------------------------------- 场景 A
def test_run_diagnosis_conflict_raises_and_runs_runner_once(tmp_path: Path):
    service, wrapper, container, run_calls = _build_service_with_conflict(
        tmp_path, conflict_index=1, competitor=_tag_reporter
    )
    try:
        case = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REPORTER,
        )
        assert case.version == 1

        with pytest.raises(ConcurrentUpdateError):
            service.run_diagnosis(case.diagnosis_id)

        # Runner 恰好 1 次：冲突发生在 Runner 之后，且没有重放 ToolLoop。
        assert run_calls["count"] == 1
        assert len(wrapper.update_calls) == 1
        assert wrapper.conflict_injected is True
    finally:
        container.close()


def test_run_diagnosis_conflict_preserves_winner(tmp_path: Path):
    service, wrapper, container, run_calls = _build_service_with_conflict(
        tmp_path, conflict_index=1, competitor=_tag_reporter
    )
    try:
        case = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REPORTER,
        )

        with pytest.raises(ConcurrentUpdateError):
            service.run_diagnosis(case.diagnosis_id)

        final = container.repository.get(case.diagnosis_id)
        # 赢家数据与版本保留
        assert final.reporter == "competitor-winner"
        assert final.version == 2
        # 失败请求没有覆盖赢家：状态仍是 created，无 Evidence / Conclusion
        assert final.status is SecurityDiagnosisStatus.CREATED
        assert final.evidence == []
        assert final.conclusion is None
    finally:
        container.close()


# ---------------------------------------------------------------- 场景 B
def test_review_conflict_raises_exact_concurrent_update(tmp_path: Path):
    """精确断言 ConcurrentUpdateError，而不是终态导致的其它异常。"""
    service, wrapper, container, run_calls = _build_service_with_conflict(
        tmp_path, conflict_index=2, competitor=_competitor_confirms
    )
    try:
        case = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REPORTER,
        )
        service.run_diagnosis(case.diagnosis_id)  # update #1 → version 2

        waiting = container.repository.get(case.diagnosis_id)
        assert waiting.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
        assert waiting.version == 2

        with pytest.raises(ConcurrentUpdateError) as excinfo:
            service.review_diagnosis(
                diagnosis_id=case.diagnosis_id,
                action=HumanReviewAction.CONFIRM,
                reviewer=REPORTER,
                comment=LOSING_COMMENT,
            )

        # 异常类型必须是 CAS 冲突本身
        assert type(excinfo.value) is ConcurrentUpdateError
        assert wrapper.conflict_injected is True
    finally:
        container.close()


def test_review_conflict_does_not_persist_losing_review(tmp_path: Path):
    service, wrapper, container, run_calls = _build_service_with_conflict(
        tmp_path, conflict_index=2, competitor=_competitor_confirms
    )
    try:
        case = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REPORTER,
        )
        service.run_diagnosis(case.diagnosis_id)  # update #1 → version 2

        with pytest.raises(ConcurrentUpdateError):
            service.review_diagnosis(
                diagnosis_id=case.diagnosis_id,
                action=HumanReviewAction.CONFIRM,
                reviewer=REPORTER,
                comment=LOSING_COMMENT,
            )

        final = container.repository.get(case.diagnosis_id)

        # 输家的 review 没有落库
        assert LOSING_COMMENT not in [review.comment for review in final.reviews]
        assert REPORTER not in [review.reviewer for review in final.reviews]

        # 赢家的 review / 状态 / 版本全部保留
        assert [review.reviewer for review in final.reviews] == ["competitor"]
        assert final.status is SecurityDiagnosisStatus.CONFIRMED
        assert final.version == 3
    finally:
        container.close()


def test_review_conflict_does_not_replay_human_review(tmp_path: Path):
    """HumanReview 不得被自动重放：update 调用次数等于业务次数。"""
    service, wrapper, container, run_calls = _build_service_with_conflict(
        tmp_path, conflict_index=2, competitor=_competitor_confirms
    )
    try:
        case = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REPORTER,
        )
        service.run_diagnosis(case.diagnosis_id)

        with pytest.raises(ConcurrentUpdateError):
            service.review_diagnosis(
                diagnosis_id=case.diagnosis_id,
                action=HumanReviewAction.CONFIRM,
                reviewer=REPORTER,
                comment=LOSING_COMMENT,
            )

        # run 一次 + review 一次 = 2 次 update，没有第 3 次重放
        assert len(wrapper.update_calls) == 2
        # Runner 只跑了 run_diagnosis 那一次
        assert run_calls["count"] == 1
    finally:
        container.close()
