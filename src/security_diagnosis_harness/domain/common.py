"""领域层通用工具：ID 生成与时间。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def new_id(prefix: str) -> str:
    """生成带前缀的可读领域 ID。"""
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    """统一的 UTC 当前时间。"""
    return datetime.now(UTC)


def canonical_json(value: Any) -> str:
    """生成稳定排序的 JSON 文本，用于内容 hash。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(payload: Mapping[str, Any]) -> str:
    """对可序列化内容计算 sha256。"""
    return sha256_text(canonical_json(dict(payload)))
