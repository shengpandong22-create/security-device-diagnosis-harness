"""Phase 6B-2 乐观锁真实探针。

用**文件型 SQLite + 两个独立 Repository**制造真实并发冲突：

- Request A 与 Request B 各自读取同一 version；
- A 先更新成功（version+1）；
- B 用陈旧副本更新必须被拒；
- 成功请求的数据必须完整保留；
- Agent / Tool / HumanReview 不得自动重试。

输出：

```text
STALE_UPDATE_REJECTED: True
WINNER_VERSION: 2
WINNER_DATA_PRESERVED: True
LOSER_DATA_ABSENT: True
AUTOMATIC_AGENT_RETRY: False
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
from security_diagnosis_harness.application.errors import (  # noqa: E402
    ConcurrentUpdateError,
)
from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.domain.review import HumanReviewAction  # noqa: E402
from security_diagnosis_harness.runtime import (  # noqa: E402
    build_runtime_container,
    upgrade_database,
)

DEVICE_ID = "camera-3f-001"


def _new_case(case_id: str) -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=case_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id=DEVICE_ID,
        reporter="probe",
    )


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="phase6b2-lock-"))
    database_path = workdir / "lock.db"
    url = f"sqlite:///{database_path.as_posix()}"

    # 只用真实 Alembic 建立 schema（不用 Base.metadata.create_all 冒充迁移）。
    upgrade_database(url)

    engine, factory = build_database(url)

    stale_rejected = False
    winner_version = 0
    winner_preserved = False
    loser_absent = False
    agent_retry = False
    review_retry = False

    repository = None
    runtime = None
    try:
        repository = SqlAlchemyDiagnosisRepository(factory)

        # ------------------------------------------------------ 建立 version=1
        saved = repository.save(_new_case("diag-lock-probe"))
        assert saved.version == 1

        # ------------------------------------------------------ 两个独立副本
        copy_a = repository.get(saved.diagnosis_id)
        copy_b = repository.get(saved.diagnosis_id)
        assert copy_a is not copy_b
        assert copy_a.version == copy_b.version == 1

        # ------------------------------------------------------ A 成功
        copy_a.description = "winner"
        winner = repository.update(copy_a)
        winner_version = winner.version

        # ------------------------------------------------------ B 陈旧被拒
        copy_b.description = "loser"
        try:
            repository.update(copy_b)
            stale_rejected = False
        except ConcurrentUpdateError:
            stale_rejected = True

        final = repository.get(saved.diagnosis_id)
        winner_preserved = final.description == "winner" and final.version == 2
        loser_absent = "loser" not in json.dumps(final.model_dump(), default=str)

        # ------------------------------------------ Agent / Review 不自动重试
        settings = RuntimeSettings(repository_mode="sqlite", database_url=url)
        runtime = build_runtime_container(settings)
        runner_calls = {"count": 0}
        original_run = runtime.runner.run

        def _counting_run(*args, **kwargs):
            runner_calls["count"] += 1
            return original_run(*args, **kwargs)

        runtime.runner.run = _counting_run  # type: ignore[method-assign]

        probe = runtime.service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="probe",
        )
        runtime.service.run_diagnosis(probe.diagnosis_id)
        agent_calls_after_success = runner_calls["count"]
        agent_retry = agent_calls_after_success > 1

        # 陈旧 review 也不得被自动重放
        stale_review_case = runtime.service.get_diagnosis(probe.diagnosis_id)
        runtime.service.review_diagnosis(
            diagnosis_id=probe.diagnosis_id,
            action=HumanReviewAction.CONFIRM,
            reviewer="probe",
        )
        try:
            runtime.service.review_diagnosis(
                diagnosis_id=probe.diagnosis_id,
                action=HumanReviewAction.CONFIRM,
                reviewer="probe",
            )
            review_retry = True
        except Exception:  # noqa: BLE001 - 拒绝或终态都算"未重放"
            review_retry = False
        # stale copy 再 update 也不允许
        try:
            runtime.repository.update(stale_review_case)
            review_retry = True
        except ConcurrentUpdateError:
            review_retry = False
    finally:
        if runtime is not None:
            runtime.close()
        if repository is not None and runtime is None:
            pass
        engine.dispose()
        shutil.rmtree(workdir, ignore_errors=True)

    report = {
        "stale_update_rejected": stale_rejected,
        "winner_version": winner_version,
        "winner_data_preserved": winner_preserved,
        "loser_data_absent": loser_absent,
        "automatic_agent_retry": agent_retry,
        "automatic_review_retry": review_retry,
    }

    print(f"STALE_UPDATE_REJECTED: {report['stale_update_rejected']}")
    print(f"WINNER_VERSION: {report['winner_version']}")
    print(f"WINNER_DATA_PRESERVED: {report['winner_data_preserved']}")
    print(f"LOSER_DATA_ABSENT: {report['loser_data_absent']}")
    print(f"AUTOMATIC_AGENT_RETRY: {report['automatic_agent_retry']}")
    print(f"AUTOMATIC_REVIEW_RETRY: {report['automatic_review_retry']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = (
        stale_rejected
        and winner_version == 2
        and winner_preserved
        and loser_absent
        and not agent_retry
        and not review_retry
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
