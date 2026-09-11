"""运行只读一致性扫描并输出 JSON 摘要与 Markdown 报告。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402


def main() -> int:
    with build_runtime_container() as container:
        report = container.consistency_scanner.scan()
    output = Path("demo-output/phase6-consistency-report.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.to_markdown(), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": report.ok,
                "diagnosis_count": report.diagnosis_count,
                "knowledge_count": report.knowledge_count,
                "blocking_count": report.blocking_count,
                "report": output.as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
