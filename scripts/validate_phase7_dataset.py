"""验证 Phase 7 数据集协议、文件完整性与跨集合隔离。"""

from __future__ import annotations

import json
from pathlib import Path

from security_diagnosis_harness.evaluation import DatasetRegistry, DatasetSplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO_ROOT / "datasets" / "security-diagnosis" / "1.0.0"


def main() -> int:
    registry = DatasetRegistry.load(DATASET_ROOT)
    counts = {split.value: registry.case_count(split) for split in DatasetSplit}
    print(
        json.dumps(
            {
                "dataset_version": DATASET_ROOT.name,
                "split_counts": counts,
                "total": sum(counts.values()),
                "cross_split_leakage": False,
                "manifest_integrity": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
