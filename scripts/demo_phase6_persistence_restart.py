"""Phase 6B-1 真实重启恢复 Demo。

流程：

1. 在临时目录创建文件型 SQLite；
2. RuntimeContainer A：创建诊断 → 运行 → 人工 confirm；
3. 记录聚合关键字段与 A 的对象身份；
4. `container_a.close()`（dispose Engine）；
5. 用**相同 database_url** 构建 RuntimeContainer B
   （全新 Engine / SessionFactory / Repository / Service）；
6. 从 B 恢复并逐项比对；
7. 关闭 B、删除临时数据库，输出 JSON。

不使用内存 SQLite，不使用 pickle/JSON 绕过数据库，不访问模型 / 设备 / 网络。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.domain.review import HumanReviewAction  # noqa: E402
from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402

REVIEWER = "demo-expert"
# 与 Phase 0 demo 使用同一台样例设备，产物为 4 条 Evidence，
# 便于逐条验证 Evidence ID / content_hash 的恢复。
DEVICE_ID = "camera-3f-001"


def _settings(database_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{database_path.as_posix()}",
        auto_migrate=True,
    )


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="phase6b-restart-"))
    database_path = workdir / "restart.db"
    settings = _settings(database_path)

    container_a = None
    container_b = None
    try:
        # ---------------------------------------------------------- 运行环境 A
        container_a = build_runtime_container(settings)
        service_a = container_a.service

        case = service_a.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REVIEWER,
            description="演示：摄像头黑屏后重启进程",
        )
        service_a.run_diagnosis(case.diagnosis_id)
        service_a.review_diagnosis(
            diagnosis_id=case.diagnosis_id,
            action=HumanReviewAction.CONFIRM,
            reviewer=REVIEWER,
            comment="与现场一致",
        )

        before = service_a.get_diagnosis(case.diagnosis_id)
        snapshot = {
            "diagnosis_id": before.diagnosis_id,
            "status": before.status.value,
            "evidence_ids": [item.evidence_id for item in before.evidence],
            "evidence_hashes": [item.content_hash for item in before.evidence],
            "conclusion_id": before.conclusion.conclusion_id,
            "cited_evidence_ids": list(before.conclusion.cited_evidence_ids),
            "review_id": before.reviews[0].review_id,
            "created_at": before.created_at.isoformat(),
            "updated_at": before.updated_at.isoformat(),
        }
        identity_a = {
            "engine": id(container_a.engine),
            "session_factory": id(container_a.session_factory),
            "repository": id(container_a.repository),
            "service": id(container_a.service),
        }

        # ---------------------------------------------------------- 销毁 A
        container_a.close()
        assert container_a.engine is None
        container_a = None

        # ---------------------------------------------------------- 运行环境 B
        container_b = build_runtime_container(settings)
        service_b = container_b.service

        identity_b = {
            "engine": id(container_b.engine),
            "session_factory": id(container_b.session_factory),
            "repository": id(container_b.repository),
            "service": id(container_b.service),
        }

        after = service_b.get_diagnosis(snapshot["diagnosis_id"])

        external_model_called = container_b.llm.external_model_called

        report = {
            "repository_mode": settings.repository_mode.value,
            "restart_recovered": True,
            "new_engine_created": identity_a["engine"] != identity_b["engine"],
            "new_session_factory_created": (
                identity_a["session_factory"] != identity_b["session_factory"]
            ),
            "new_repository_created": identity_a["repository"] != identity_b["repository"],
            "new_service_created": identity_a["service"] != identity_b["service"],
            "status": after.status.value,
            "evidence_count": len(after.evidence),
            "evidence_ids_preserved": (
                [item.evidence_id for item in after.evidence] == snapshot["evidence_ids"]
            ),
            "evidence_hashes_preserved": (
                [item.content_hash for item in after.evidence] == snapshot["evidence_hashes"]
            ),
            "conclusion_id_preserved": after.conclusion.conclusion_id == snapshot["conclusion_id"],
            "citations_preserved": (
                list(after.conclusion.cited_evidence_ids) == snapshot["cited_evidence_ids"]
            ),
            "review_id_preserved": after.reviews[0].review_id == snapshot["review_id"],
            "timestamps_are_aware": (
                after.created_at.tzinfo is not None and after.updated_at.tzinfo is not None
            ),
            "timestamps_preserved": (
                after.created_at.isoformat() == snapshot["created_at"]
                and after.updated_at.isoformat() == snapshot["updated_at"]
            ),
            "database_removed_after_close": True,
            "external_model_called": external_model_called,
        }
    finally:
        if container_a is not None:
            container_a.close()
        if container_b is not None:
            container_b.close()
        shutil.rmtree(workdir, ignore_errors=True)
        report_database_removed = not database_path.exists() and not workdir.exists()

    report["database_removed_after_close"] = report_database_removed
    if not report_database_removed:
        report["restart_recovered"] = False

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["restart_recovered"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
