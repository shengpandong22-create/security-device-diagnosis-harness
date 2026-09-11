"""Phase 6C-3 SQLite 安全备份与恢复验收。"""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from security_diagnosis_harness.adapters.persistence.backup import (
    BackupError,
    BackupManifest,
    create_backup,
    restore_backup,
)
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import KnowledgeReviewAction
from security_diagnosis_harness.domain.review import HumanReviewAction
from security_diagnosis_harness.runtime import (
    ALEMBIC_INI_PATH,
    MIGRATIONS_DIR,
    build_runtime_container,
)


def _settings(path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        repository_mode="sqlite", database_url=f"sqlite:///{path.as_posix()}"
    )


def test_backup_restore_recovers_snapshot_and_keeps_recovery_copy(tmp_path: Path):
    database = tmp_path / "live.db"
    settings = _settings(database)
    container = build_runtime_container(settings)
    first = container.service.create_diagnosis(
        device_id="camera-3f-001",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="before-backup",
    )
    container.service.run_diagnosis(first.diagnosis_id)
    container.service.review_diagnosis(
        first.diagnosis_id, HumanReviewAction.CONFIRM, "diagnosis-reviewer"
    )
    knowledge = container.knowledge_service.generate_and_save(
        first.diagnosis_id, "knowledge-curator"
    )
    knowledge = container.knowledge_service.review(
        knowledge.knowledge_id,
        KnowledgeReviewAction.CONFIRM,
        "knowledge-reviewer",
    )
    manifest_path = create_backup(settings.database_url, tmp_path / "backups")
    second = container.service.create_diagnosis(
        device_id="camera-3f-001",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="after-backup",
    )
    container.close()

    recovery = restore_backup(settings.database_url, manifest_path, runtime=container)
    assert recovery is not None and recovery.is_file()

    with build_runtime_container(settings) as restored:
        recovered = restored.repository.get(first.diagnosis_id)
        assert recovered.evidence
        assert recovered.conclusion is not None
        assert recovered.reviews
        assert restored.knowledge_repository.get(knowledge.knowledge_id).reviews
        assert restored.audit_repository.list_for_entity(first.diagnosis_id)
        assert restored.audit_repository.list_for_entity(knowledge.knowledge_id)
        assert not restored.repository.exists(second.diagnosis_id)


def test_manifest_contains_required_integrity_metadata(tmp_path: Path):
    database = tmp_path / "live.db"
    settings = _settings(database)
    with build_runtime_container(settings):
        manifest_path = create_backup(settings.database_url, tmp_path / "backups")
    manifest = BackupManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert manifest.schema_revision == "0003"
    assert manifest.size_bytes > 0
    assert len(manifest.sha256) == 64
    assert manifest.created_at.tzinfo is not None


def test_restore_rejects_running_runtime(tmp_path: Path):
    database = tmp_path / "live.db"
    settings = _settings(database)
    with build_runtime_container(settings) as container:
        manifest = create_backup(settings.database_url, tmp_path / "backups")
        with pytest.raises(BackupError, match="未关闭"):
            restore_backup(settings.database_url, manifest, runtime=container)


def test_restore_rejects_tampered_backup_without_touching_target(tmp_path: Path):
    database = tmp_path / "live.db"
    settings = _settings(database)
    with build_runtime_container(settings) as container:
        manifest_path = create_backup(settings.database_url, tmp_path / "backups")
    original = database.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup = manifest_path.parent / manifest["backup_file"]
    backup.write_bytes(backup.read_bytes() + b"tampered")
    with pytest.raises(BackupError, match="checksum"):
        restore_backup(settings.database_url, manifest_path, runtime=container)
    assert database.read_bytes() == original


@pytest.mark.parametrize(
    "url", ["sqlite:///:memory:", "postgresql://localhost/db", "not-a-url"]
)
def test_backup_rejects_unsupported_database(url: str, tmp_path: Path):
    with pytest.raises(BackupError):
        create_backup(url, tmp_path)


def test_restore_rejects_invalid_manifest_as_controlled_error(tmp_path: Path):
    database = tmp_path / "live.db"
    settings = _settings(database)
    container = build_runtime_container(settings)
    container.close()
    manifest = tmp_path / "invalid.manifest.json"
    manifest.write_text("not-json", encoding="utf-8")
    with pytest.raises(BackupError, match="manifest"):
        restore_backup(settings.database_url, manifest, runtime=container)


def test_restore_rejects_backup_from_unsupported_revision(tmp_path: Path):
    source = tmp_path / "old.db"
    source_url = f"sqlite:///{source.as_posix()}"
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", source_url)
    command.upgrade(config, "0002")
    manifest = create_backup(source_url, tmp_path / "backups")

    target = tmp_path / "target.db"
    settings = _settings(target)
    container = build_runtime_container(settings)
    container.close()
    with pytest.raises(BackupError, match="revision"):
        restore_backup(settings.database_url, manifest, runtime=container)


def test_restore_rejects_runtime_owner_for_another_database(tmp_path: Path):
    source = tmp_path / "source.db"
    source_settings = _settings(source)
    source_runtime = build_runtime_container(source_settings)
    manifest = create_backup(source_settings.database_url, tmp_path / "backups")
    source_runtime.close()

    other = build_runtime_container(_settings(tmp_path / "other.db"))
    other.close()
    with pytest.raises(BackupError, match="不匹配"):
        restore_backup(source_settings.database_url, manifest, runtime=other)
