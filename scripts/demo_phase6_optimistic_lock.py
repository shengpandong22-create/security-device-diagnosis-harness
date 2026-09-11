"""Phase 6B-2 乐观锁真实探针。

两部分：

1. **Repository 级**：文件型 SQLite + 两个独立副本，B 用陈旧副本更新被拒。
2. **Application 级（真实路径）**：
   - 场景 A：`run_diagnosis` 在最终 update 前被竞争者抢先 CAS，
     断言 Runner 恰好执行 1 次且没有重放；
   - 场景 B：`review_diagnosis` 在最终 update 前被竞争者抢先确认，
     断言输家 review 不落库、赢家保留。

只有**真正捕获到** `ConcurrentUpdateError` 才算观察到冲突；
没有捕获则脚本退出非 0（不用 `except Exception` 冒充）。

输出：

```text
STALE_UPDATE_REJECTED: True
WINNER_VERSION: 2
WINNER_DATA_PRESERVED: True
LOSER_DATA_ABSENT: True
AGENT_CONFLICT_OBSERVED: True
AUTOMATIC_AGENT_RETRY: False
REVIEW_CONFLICT_OBSERVED: True
AUTOMATIC_REVIEW_RETRY: False
```
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.adapters.persistence import (  # noqa: E402
    SqlAlchemyDiagnosisRepository,
    build_database,
)
from security_diagnosis_harness.application.diagnoses import (  # noqa: E402
    SecurityDiagnosisApplicationService,
)
from security_diagnosis_harness.application.errors import (  # noqa: E402
    ConcurrentUpdateError,
)
from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase  # noqa: E402
from security_diagnosis_harness.domain.enums import (  # noqa: E402
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.review import (  # noqa: E402
    HumanReview,
    HumanReviewAction,
)
from security_diagnosis_harness.runtime import (  # noqa: E402
    build_runtime_container,
    upgrade_database,
)

DEVICE_ID = "camera-3f-001"
LOSING_COMMENT = "应该失败的审核"


class _ConflictRepository:
    """在指定第 N 次 update 前注入一次竞争者的合法 CAS 更新。"""

    def __init__(self, inner, *, conflict_index: int, competitor) -> None:
        self._inner = inner
        self._target = conflict_index
        self._competitor = competitor
        self.update_calls = 0
        self.conflict_injected = False

    def get(self, diagnosis_id):
        return self._inner.get(diagnosis_id)

    def list(self):
        return self._inner.list()

    def exists(self, diagnosis_id):
        return self._inner.exists(diagnosis_id)

    def count(self):
        return self._inner.count()

    def save(self, case):
        return self._inner.save(case)

    def update(self, case):
        self.update_calls += 1
        if self.update_calls == self._target and not self.conflict_injected:
            competitor = self._inner.get(case.diagnosis_id)
            self._competitor(competitor)
            self._inner.update(competitor)
            self.conflict_injected = True
        return self._inner.update(case)


def _tag_reporter(case) -> None:
    case.reporter = "competitor-winner"


def _competitor_confirms(case) -> None:
    case.apply_human_review(
        HumanReview(
            diagnosis_id=case.diagnosis_id,
            action=HumanReviewAction.CONFIRM,
            reviewer="competitor",
            comment="竞争者先确认",
        )
    )


def _new_case(case_id: str) -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=case_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id=DEVICE_ID,
        reporter="probe",
    )


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="phase6b2-lock-"))
    url = f"sqlite:///{(workdir / 'lock.db').as_posix()}"

    stale_rejected = False
    winner_version = 0
    winner_preserved = False
    loser_absent = False

    agent_conflict_observed = False
    agent_retry = True  # 观察到冲突后才会被判定
    run_calls = 0

    review_conflict_observed = False
    review_retry = True
    losing_review_absent = False

    container = None
    engine = None
    try:
        # 只用真实 Alembic 建立 schema（不用 Base.metadata.create_all 冒充迁移）。
        upgrade_database(url)
        engine, factory = build_database(url)

        # ---------------------------------------------- Repository 级冲突
        repository = SqlAlchemyDiagnosisRepository(factory)
        saved = repository.save(_new_case("diag-lock-probe"))
        assert saved.version == 1

        copy_a = repository.get(saved.diagnosis_id)
        copy_b = repository.get(saved.diagnosis_id)
        assert copy_a is not copy_b
        assert copy_a.version == copy_b.version == 1

        copy_a.description = "winner"
        winner = repository.update(copy_a)
        winner_version = winner.version

        copy_b.description = "loser"
        try:
            repository.update(copy_b)
        except ConcurrentUpdateError:
            stale_rejected = True

        final = repository.get(saved.diagnosis_id)
        winner_preserved = final.description == "winner" and final.version == 2
        loser_absent = "loser" not in json.dumps(final.model_dump(), default=str)
        engine.dispose()
        engine = None

        # ---------------------------------------------- 场景 A：run 冲突
        settings = RuntimeSettings(repository_mode="sqlite", database_url=url)
        container = build_runtime_container(settings)

        wrapper = _ConflictRepository(
            container.repository, conflict_index=1, competitor=_tag_reporter
        )
        service = SecurityDiagnosisApplicationService(
            repository=wrapper,
            runner=container.runner,
            registry=container.registry,
            gateway=container.gateway,
            citation_policy=container.citation_policy,
            supported_fault_types=container.service.supported_fault_types,
        )

        calls = {"count": 0}
        original_run = container.runner.run

        def _counting_run(*args, **kwargs):
            calls["count"] += 1
            return original_run(*args, **kwargs)

        container.runner.run = _counting_run  # type: ignore[method-assign]

        case_a = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="probe",
        )
        try:
            service.run_diagnosis(case_a.diagnosis_id)
        except ConcurrentUpdateError:
            agent_conflict_observed = True

        run_calls = calls["count"]
        # 冲突已观察到且 Runner 只跑了 1 次 → 没有自动重试
        agent_retry = not (agent_conflict_observed and run_calls == 1)

        # ---------------------------------------------- 场景 B：review 冲突
        container.close()
        container = build_runtime_container(settings)

        review_wrapper = _ConflictRepository(
            container.repository, conflict_index=2, competitor=_competitor_confirms
        )
        review_service = SecurityDiagnosisApplicationService(
            repository=review_wrapper,
            runner=container.runner,
            registry=container.registry,
            gateway=container.gateway,
            citation_policy=container.citation_policy,
            supported_fault_types=container.service.supported_fault_types,
        )

        case_b = review_service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="probe",
        )
        review_service.run_diagnosis(case_b.diagnosis_id)  # update #1 → version 2

        try:
            review_service.review_diagnosis(
                diagnosis_id=case_b.diagnosis_id,
                action=HumanReviewAction.CONFIRM,
                reviewer="probe",
                comment=LOSING_COMMENT,
            )
        except ConcurrentUpdateError:
            review_conflict_observed = True

        final_b = container.repository.get(case_b.diagnosis_id)
        losing_review_absent = LOSING_COMMENT not in [
            review.comment for review in final_b.reviews
        ]
        winner_kept = (
            [review.reviewer for review in final_b.reviews] == ["competitor"]
            and final_b.status is SecurityDiagnosisStatus.CONFIRMED
        )
        review_retry = not (
            review_conflict_observed
            and losing_review_absent
            and winner_kept
            and review_wrapper.update_calls == 2
        )
    finally:
        if container is not None:
            container.close()
        if engine is not None:
            engine.dispose()
        shutil.rmtree(workdir, ignore_errors=True)

    report = {
        "stale_update_rejected": stale_rejected,
        "winner_version": winner_version,
        "winner_data_preserved": winner_preserved,
        "loser_data_absent": loser_absent,
        "agent_conflict_observed": agent_conflict_observed,
        "automatic_agent_retry": agent_retry,
        "runner_call_count": run_calls,
        "review_conflict_observed": review_conflict_observed,
        "automatic_review_retry": review_retry,
    }

    print(f"STALE_UPDATE_REJECTED: {report['stale_update_rejected']}")
    print(f"WINNER_VERSION: {report['winner_version']}")
    print(f"WINNER_DATA_PRESERVED: {report['winner_data_preserved']}")
    print(f"LOSER_DATA_ABSENT: {report['loser_data_absent']}")
    print(f"AGENT_CONFLICT_OBSERVED: {report['agent_conflict_observed']}")
    print(f"AUTOMATIC_AGENT_RETRY: {report['automatic_agent_retry']}")
    print(f"REVIEW_CONFLICT_OBSERVED: {report['review_conflict_observed']}")
    print(f"AUTOMATIC_REVIEW_RETRY: {report['automatic_review_retry']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = (
        stale_rejected
        and winner_version == 2
        and winner_preserved
        and loser_absent
        and agent_conflict_observed
        and not agent_retry
        and review_conflict_observed
        and not review_retry
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
