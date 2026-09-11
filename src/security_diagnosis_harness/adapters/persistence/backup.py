"""文件型 SQLite 的一致备份、校验与显式恢复。"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from security_diagnosis_harness.config import RuntimeSettings

SUPPORTED_SCHEMA_REVISION = "0003"


class BackupError(RuntimeError):
    """备份或恢复未通过安全校验。"""


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backup_file: str = Field(min_length=1)
    schema_revision: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class RuntimeOwner(Protocol):
    @property
    def closed(self) -> bool: ...

    @property
    def settings(self) -> RuntimeSettings: ...


def sqlite_path(database_url: str) -> Path:
    """解析受支持的文件型 SQLite URL。"""
    prefixes = ("sqlite+pysqlite:///", "sqlite:///")
    prefix = next((item for item in prefixes if database_url.startswith(item)), None)
    if prefix is None:
        raise BackupError("只支持文件型 SQLite URL")
    raw = database_url[len(prefix) :]
    if not raw or raw == ":memory:":
        raise BackupError("内存 SQLite 不支持备份或恢复")
    return Path(raw).expanduser().resolve()


def create_backup(database_url: str, backup_directory: str | Path) -> Path:
    source = sqlite_path(database_url)
    if not source.is_file():
        raise BackupError("源数据库不存在")
    destination_dir = Path(backup_directory).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = f"security-diagnosis-{uuid4().hex}"
    backup_path = destination_dir / f"{stem}.sqlite3"

    try:
        with closing(sqlite3.connect(source)) as source_db, closing(
            sqlite3.connect(backup_path)
        ) as backup_db:
            source_db.backup(backup_db)
        revision = _validate_database(backup_path)
        manifest = BackupManifest(
            backup_file=backup_path.name,
            schema_revision=revision,
            size_bytes=backup_path.stat().st_size,
            sha256=_sha256(backup_path),
            created_at=datetime.now(UTC),
        )
        manifest_path = destination_dir / f"{stem}.manifest.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return manifest_path
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise


def restore_backup(
    database_url: str,
    manifest_path: str | Path,
    *,
    runtime: RuntimeOwner,
) -> Path | None:
    """校验备份后原子替换目标；调用方必须先关闭 RuntimeContainer。"""
    if not runtime.closed:
        raise BackupError("Runtime 未关闭，拒绝恢复数据库")
    target = sqlite_path(database_url)
    if sqlite_path(runtime.settings.database_url) != target:
        raise BackupError("Runtime owner 与恢复目标不匹配")
    manifest_file = Path(manifest_path).expanduser().resolve()
    try:
        manifest = BackupManifest.model_validate_json(
            manifest_file.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise BackupError("备份 manifest 无法读取或格式非法") from exc
    backup = (manifest_file.parent / manifest.backup_file).resolve()
    if backup.parent != manifest_file.parent or not backup.is_file():
        raise BackupError("manifest 引用的备份文件非法")
    if backup.stat().st_size != manifest.size_bytes or _sha256(backup) != manifest.sha256:
        raise BackupError("备份文件大小或 checksum 校验失败")
    revision = _validate_database(backup)
    if revision != manifest.schema_revision or revision != SUPPORTED_SCHEMA_REVISION:
        raise BackupError("备份 schema revision 不受支持")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{uuid4().hex}.restore")
    recovery: Path | None = None
    try:
        shutil.copy2(backup, staging)
        _validate_database(staging)
        if target.exists():
            recovery = target.with_name(f"{target.name}.pre-restore-{uuid4().hex}.sqlite3")
            shutil.copy2(target, recovery)
        os.replace(staging, target)
        return recovery
    except Exception:
        staging.unlink(missing_ok=True)
        raise


def _validate_database(path: Path) -> str:
    try:
        with closing(
            sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        ) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise BackupError("SQLite integrity_check 未通过")
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error as exc:
        raise BackupError("备份不是可读取的项目 SQLite 数据库") from exc
    if row is None or not row[0]:
        raise BackupError("备份缺少 Alembic revision")
    return str(row[0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
