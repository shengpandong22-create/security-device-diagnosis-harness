from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify_release.ps1"
WORKFLOW = ROOT / ".github/workflows/verify.yml"
WORKTREE_HELPER = ROOT / "scripts/verify_release_worktree.ps1"


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _initialized_repository(path: Path) -> None:
    assert _git(path, "init").returncode == 0
    assert _git(path, "config", "user.email", "verify@example.invalid").returncode == 0
    assert _git(path, "config", "user.name", "Verify Test").returncode == 0
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    assert _git(path, "add", "tracked.txt").returncode == 0
    assert _git(path, "commit", "-m", "base").returncode == 0


def test_release_verification_rejects_real_call_environment():
    environment = os.environ.copy()
    environment["SECURITY_DIAGNOSIS_EVAL_API_KEY"] = "test-must-be-rejected"

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(SCRIPT), "-CheckEnvironmentOnly"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    assert "SECURITY_DIAGNOSIS_EVAL_API_KEY" in completed.stderr
    assert "test-must-be-rejected" not in completed.stdout + completed.stderr


def test_release_verification_accepts_clean_environment():
    environment = os.environ.copy()
    for name in (
        "SECURITY_DIAGNOSIS_EVAL_BASE_URL",
        "SECURITY_DIAGNOSIS_EVAL_MODEL",
        "SECURITY_DIAGNOSIS_EVAL_API_KEY",
        "SECURITY_DIAGNOSIS_EVAL_INTEGRITY_KEY",
        "SECURITY_DIAGNOSIS_DEVICE_HOST",
        "SECURITY_DIAGNOSIS_DEVICE_USERNAME",
        "SECURITY_DIAGNOSIS_DEVICE_PASSWORD",
        "SECURITY_DIAGNOSIS_DEVICE_RTSP_PORT",
        "SECURITY_DIAGNOSIS_LAB_CREDENTIAL",
        "SECURITY_DIAGNOSIS_API_TOKEN",
        "SECURITY_DIAGNOSIS_API_REVIEWER_TOKEN",
    ):
        environment.pop(name, None)

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(SCRIPT), "-CheckEnvironmentOnly"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "VERIFY_ENVIRONMENT_OK" in completed.stdout


def test_ci_workflow_is_no_secret_and_offline_scoped():
    content = WORKFLOW.read_text(encoding="utf-8")

    assert "uv sync --frozen" in content
    assert "uv run ruff check ." in content
    assert "uv run pytest" in content
    assert "scripts/eval_phase9a_simulator_e2e.py" in content
    assert "scripts/eval_adversarial_cases.py" in content
    assert "secrets." not in content
    assert "device_lab.ps1" not in content
    assert "eval_phase7_real_model.py" not in content
    assert "probe_phase11d_compatibility.py" not in content
    assert content.index("git diff --check") < content.index("git diff --exit-code")


def test_ci_clean_tree_gate_rejects_a_well_formed_tracked_change(tmp_path):
    _initialized_repository(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")

    assert _git(tmp_path, "diff", "--check").returncode == 0
    assert _git(tmp_path, "diff", "--exit-code").returncode == 1


def test_worktree_guard_allows_an_unchanged_preexisting_diff(tmp_path):
    _initialized_repository(tmp_path)
    (tmp_path / "tracked.txt").write_text("user change\n", encoding="utf-8")
    command = (
        f". '{WORKTREE_HELPER}'; "
        f"$before=Get-TrackedWorktreeSnapshot -RepositoryRoot '{tmp_path}'; "
        f"$after=Get-TrackedWorktreeSnapshot -RepositoryRoot '{tmp_path}'; "
        "Assert-TrackedWorktreeUnchanged -Before $before -After $after; "
        "Write-Output 'UNCHANGED_OK'"
    )

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "UNCHANGED_OK" in completed.stdout


def test_worktree_guard_rejects_changed_tracked_diff_content(tmp_path):
    _initialized_repository(tmp_path)
    command = (
        f". '{WORKTREE_HELPER}'; "
        f"$before=Get-TrackedWorktreeSnapshot -RepositoryRoot '{tmp_path}'; "
        f"Set-Content -LiteralPath '{tmp_path / 'tracked.txt'}' -Value 'changed'; "
        f"$after=Get-TrackedWorktreeSnapshot -RepositoryRoot '{tmp_path}'; "
        "Assert-TrackedWorktreeUnchanged -Before $before -After $after"
    )

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode != 0
    assert "改变了受跟踪工作树" in completed.stderr
