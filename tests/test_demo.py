"""摄像头黑屏 demo 验收。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "demo_phase0_camera_black_screen.py"


def _safe_unlink(path: Path) -> None:
    if path.exists():
        path.unlink()


def _load_demo_module():
    spec = importlib.util.spec_from_file_location("demo_phase0_camera_black_screen", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_demo() -> tuple[int, dict, Path]:
    module = _load_demo_module()
    report_path = module.REPORT_PATH
    _safe_unlink(report_path)

    captured: list[str] = []
    original_stdout = sys.stdout

    class _Capture:
        def write(self, text: str) -> int:
            captured.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    sys.stdout = _Capture()
    try:
        exit_code = module.main()
    finally:
        sys.stdout = original_stdout

    summary = json.loads("".join(captured))
    return exit_code, summary, report_path


def test_demo_runs_and_prints_summary():
    exit_code, summary, _ = run_demo()

    assert exit_code == 0
    for key in (
        "diagnosis_id",
        "status",
        "evidence_count",
        "conclusion_confidence",
        "cited_evidence_ids",
        "human_action",
        "external_model_called",
        "report",
    ):
        assert key in summary


def test_demo_does_not_call_external_model():
    _, summary, _ = run_demo()

    assert summary["external_model_called"] is False


def test_demo_generates_report_file():
    _, summary, report_path = run_demo()

    assert Path(summary["report"]) == Path("demo-output/phase0-camera-black-screen-report.md")
    assert report_path.exists()

    content = report_path.read_text(encoding="utf-8")
    assert content.startswith("# 安防设备诊断报告")
    assert summary["diagnosis_id"] in content
    assert "confirmed" in content

    _safe_unlink(report_path)


def test_demo_collects_at_least_three_evidence():
    _, summary, report_path = run_demo()

    assert summary["evidence_count"] >= 3
    assert summary["cited_evidence_ids"]

    _safe_unlink(report_path)


def test_demo_final_status_is_confirmed():
    _, summary, report_path = run_demo()

    assert summary["status"] == "confirmed"
    assert summary["human_action"] == "confirm"
    assert summary["conclusion_confidence"] == "probable"

    _safe_unlink(report_path)


def test_demo_is_repeatable():
    first, first_summary, first_path = run_demo()
    second, second_summary, second_path = run_demo()

    assert first == second == 0
    assert first_summary["status"] == second_summary["status"] == "confirmed"
    assert first_summary["evidence_count"] == second_summary["evidence_count"]
    assert first_summary["diagnosis_id"] != second_summary["diagnosis_id"]

    _safe_unlink(first_path)
    _safe_unlink(second_path)
