from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify_release.ps1"
WORKFLOW = ROOT / ".github/workflows/verify.yml"


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
