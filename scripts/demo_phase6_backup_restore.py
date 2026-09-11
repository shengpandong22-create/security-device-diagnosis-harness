"""Phase 6C-3 真实 SQLite 备份恢复探针。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.adapters.persistence.backup import (  # noqa: E402
    create_backup,
    restore_backup,
)
from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="phase6c-backup-"))
    database = workdir / "live.db"
    settings = RuntimeSettings(
        repository_mode="sqlite", database_url=f"sqlite:///{database.as_posix()}"
    )
    recovery = None
    try:
        container = build_runtime_container(settings)
        before = container.service.create_diagnosis(
            device_id="camera-3f-001",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="before",
        )
        manifest = create_backup(settings.database_url, workdir / "backups")
        after = container.service.create_diagnosis(
            device_id="camera-3f-001",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="after",
        )
        container.close()
        recovery = restore_backup(settings.database_url, manifest, runtime=container)
        with build_runtime_container(settings) as restored:
            result = {
                "snapshot_record_recovered": restored.repository.exists(before.diagnosis_id),
                "post_snapshot_record_absent": not restored.repository.exists(after.diagnosis_id),
                "recovery_copy_created": recovery is not None and recovery.is_file(),
                "audit_recovered": bool(
                    restored.audit_repository.list_for_entity(before.diagnosis_id)
                ),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(result.values()) else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
