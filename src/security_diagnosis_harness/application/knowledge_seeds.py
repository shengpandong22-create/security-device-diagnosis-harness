"""手工知识种子清单的严格离线加载。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from security_diagnosis_harness.domain.knowledge import ManualKnowledgeSeed

_SEEDS = TypeAdapter(list[ManualKnowledgeSeed])


def load_manual_knowledge_seeds(path: str | Path) -> list[ManualKnowledgeSeed]:
    """读取并校验完整清单；格式或内容摘要错误时整体拒绝。"""
    raw = Path(path).read_text(encoding="utf-8")
    return _SEEDS.validate_python(json.loads(raw))


__all__ = ["load_manual_knowledge_seeds"]
