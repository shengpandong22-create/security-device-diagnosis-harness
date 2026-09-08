"""Phase 0 摄像头黑屏 demo。

运行：

    uv run python scripts/demo_phase0_camera_black_screen.py

流程：创建诊断 -> 运行 -> 查询 Evidence -> 人工 confirm -> 生成 Markdown 报告 -> 打印 JSON 摘要。

全程使用 FakeLLM 与 StaticDeviceGateway：不调用真实模型，不访问真实设备。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.bootstrap.container import build_container  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.domain.review import HumanReviewAction  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "demo-output" / "phase0-camera-black-screen-report.md"

DEVICE_ID = "camera-3f-001"
REPORTER = "ops-zhang"
DESCRIPTION = "3 号楼大厅摄像头预览黑屏，录像仍在正常录制"


def main() -> int:
    """执行 demo，返回 0 表示成功。"""
    container = build_container()
    service = container.service

    case = service.create_diagnosis(
        device_id=DEVICE_ID,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter=REPORTER,
        description=DESCRIPTION,
    )

    run_result = service.run_diagnosis(case.diagnosis_id)
    evidence = service.list_evidence(case.diagnosis_id)

    review_result = service.review_diagnosis(
        diagnosis_id=case.diagnosis_id,
        action=HumanReviewAction.CONFIRM,
        reviewer="ops-li",
        comment="现场核实为主码流发布失败，同意结论并安排更换编码器。",
    )

    markdown = service.render_report(case.diagnosis_id)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(markdown, encoding="utf-8")

    summary = {
        "diagnosis_id": case.diagnosis_id,
        "status": review_result.status.value,
        "evidence_count": len(evidence),
        "conclusion_confidence": (
            run_result.conclusion.confidence.value if run_result.conclusion else None
        ),
        "cited_evidence_ids": (
            list(run_result.conclusion.cited_evidence_ids) if run_result.conclusion else []
        ),
        "human_action": review_result.action.value,
        "external_model_called": container.external_model_called,
        "report": REPORT_PATH.relative_to(REPO_ROOT).as_posix(),
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
