"""SQLite URL 解析与目录预建验收（驱动变体 / 内存库 / Windows 路径）。"""

from __future__ import annotations

from security_diagnosis_harness.adapters.persistence.database import ensure_sqlite_directory


def test_creates_parent_directory_for_plain_sqlite(tmp_path):
    target = tmp_path / "nested" / "security.db"

    ensure_sqlite_directory(f"sqlite:///{target.as_posix()}")

    assert target.parent.is_dir()


def test_creates_parent_directory_for_pysqlite_driver(tmp_path):
    target = tmp_path / "pysqlite" / "security.db"

    ensure_sqlite_directory(f"sqlite+pysqlite:///{target.as_posix()}")

    assert target.parent.is_dir()


def test_skips_memory_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    ensure_sqlite_directory("sqlite:///:memory:")

    assert list(tmp_path.rglob("*")) == []


def test_skips_uri_memory_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    ensure_sqlite_directory("sqlite:///file:memdb1?mode=memory&cache=shared")

    assert list(tmp_path.rglob("*")) == []


def test_skips_non_sqlite_dialect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    ensure_sqlite_directory("postgresql://user@localhost/app")

    assert list(tmp_path.rglob("*")) == []


def test_relative_path_creates_directory_under_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    ensure_sqlite_directory("sqlite:///./data/security.db")

    assert (tmp_path / "data").is_dir()


def test_windows_style_absolute_path_is_treated_as_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    absolute = tmp_path / "win" / "security.db"

    ensure_sqlite_directory(f"sqlite:///{absolute.as_posix()}")

    assert absolute.parent.is_dir()
