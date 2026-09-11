"""import 副作用探针（Phase 6B-1）。

在指定的空目录里以子进程方式 `import security_diagnosis_harness.api.app`，
对比导入前后的文件系统快照，输出 JSON：

```json
{
  "import_exit_code": 0,
  "files_created": [],
  "database_files_created": [],
  "data_directory_created": false
}
```

用法：

    python scripts/probe_import_side_effects.py --cwd <空目录>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _snapshot(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_dir()
    }


def _database_files(files: set[str]) -> list[str]:
    return sorted(name for name in files if name.endswith(DATABASE_SUFFIXES))


def probe(cwd: Path) -> dict:
    """在 cwd 下运行导入探针，返回副作用报告。"""
    cwd.mkdir(parents=True, exist_ok=True)
    before = _snapshot(cwd)

    completed = subprocess.run(
        [sys.executable, "-c", "import security_diagnosis_harness.api.app"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )

    after = _snapshot(cwd)
    created = sorted(after - before)

    return {
        "import_exit_code": completed.returncode,
        "files_created": created,
        "database_files_created": _database_files(set(created)),
        "data_directory_created": any(
            name == "data" or name.endswith("/data") for name in created
        ),
        "stderr": completed.stderr.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="import side-effect probe")
    parser.add_argument("--cwd", required=True, help="用于观测的空目录")
    arguments = parser.parse_args()

    report = probe(Path(arguments.cwd))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["import_exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
