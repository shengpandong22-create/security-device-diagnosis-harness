"""Phase 6B-1：import 无副作用验收。

必须用**独立子进程**+文件系统快照证明，不接受 grep 源码代替。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "scripts" / "probe_import_side_effects.py"

DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _run_probe(tmp_path: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, str(PROBE), "--cwd", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_importing_api_app_has_no_side_effects(tmp_path: Path):
    report = _run_probe(tmp_path)

    assert report["import_exit_code"] == 0
    assert report["files_created"] == []
    assert report["database_files_created"] == []
    assert report["data_directory_created"] is False


def test_importing_bootstrap_has_no_side_effects(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import security_diagnosis_harness.bootstrap.container",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.rglob("*.db")) == []
    assert not (tmp_path / "data").exists()


def test_importing_config_has_no_side_effects(tmp_path: Path):
    completed = subprocess.run(
        [sys.executable, "-c", "import security_diagnosis_harness.config"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.rglob("*.db")) == []
    assert not (tmp_path / "data").exists()


def test_module_level_app_does_not_touch_database(tmp_path: Path):
    """模块级 `app` 必须保持内存装配。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from security_diagnosis_harness.api.app import app; print(type(app).__name__)",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "FastAPI" in completed.stdout
    assert not (tmp_path / "data").exists()
    assert list(tmp_path.rglob("*.db")) == []
