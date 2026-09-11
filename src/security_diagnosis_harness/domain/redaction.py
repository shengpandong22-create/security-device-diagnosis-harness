"""统一的领域脱敏能力（Phase 6A 审计修复）。

这是全项目**唯一**的自由文本脱敏入口：

- 凭证类键名（password / token / secret / api key / ...）；
- 自由文本中的内联凭证（`password=xxx`、`Bearer xxx`、`AKIA...`、`sk-...`）；
- URL 内的 userinfo 与查询串凭证（`https://user:pass@host/?token=xxx`）；
- 安防敏感标识（卡号 / 人员 ID / 人脸 / 车牌，复用已有领域规则）。

设计约束：

- 只在 **Domain 对象构造时**执行脱敏，早于 `content_hash` 计算与任何持久化；
- 其它领域模块（device / access / alarm / knowledge）复用本模块的键名与文本规则，
  不得各写一套互相矛盾的正则；
- 脱敏是幂等的：对已脱敏文本重复执行不会再次变化。
"""

from __future__ import annotations

import re
from typing import Any

REDACTED_VALUE = "***REDACTED***"

# 凭证类键名（子串匹配，与既有 device 规则保持一致）。
_SENSITIVE_KEY_PATTERN = re.compile(
    r"password|passwd|pwd|token|secret|credential|access[_-]?key|private[_-]?key",
    re.IGNORECASE,
)

# 安防领域敏感标识（键名 + 文本内联赋值形式）。
_SENSITIVE_IDENTIFIER_KEY_PATTERN = re.compile(
    r"card[_-]?(?:no|number|id)"
    r"|face[_-]?(?:id|feature|template)"
    r"|finger[_-]?(?:print|template)"
    r"|person[_-]?id"
    r"|id[_-]?card"
    r"|license[_-]?plate"
    r"|phone|mobile|pin",
    re.IGNORECASE,
)

# 自由文本中的内联凭证 / URL 凭证。
_SENSITIVE_TEXT_PATTERN = re.compile(
    # 裸 URL（截图 / 视频 / 回放链接等，可能指向人脸或车牌原图）
    r"(?P<url>\b[a-z][a-z0-9+.\-]*://\S+)"
    # 常见云厂商 / 平台密钥前缀
    r"|(?P<aws_key>\bAKIA[0-9A-Z]{16}\b)"
    r"|(?P<openai_key>\bsk-[A-Za-z0-9_\-]{12,}\b)"
    # Authorization: Bearer xxx
    r"|(?P<bearer>\bBearer\s+[A-Za-z0-9._~+/=\-]{8,})"
    # key = value / key: value 形式的内联凭证
    r"|(?P<kv>\b(?:password|passwd|pwd|secret|client[_-]?secret|token"
    r"|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey"
    r"|access[_-]?key|private[_-]?key|auth[_-]?token)\b\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|\S+))"
    # 安防敏感标识的 key = value 形式（卡号 / 人员 ID / 车牌等）
    r"|(?P<identifier_kv>\b(?:card[_-]?(?:no|number|id)|person[_-]?id|id[_-]?card"
    r"|face[_-]?id|license[_-]?plate|phone|mobile)\b\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|\S+))"
    # URL 查询串里的凭证参数：?token=xxx&...
    r"|(?P<url_param>[?&](?:token|access_token|api_key|apikey|secret|password|pwd|signature|sig)=[^&\s]+)",
    re.IGNORECASE,
)


def is_sensitive_key(key: str) -> bool:
    """判断键名是否属于凭证类敏感字段。"""
    return _SENSITIVE_KEY_PATTERN.search(key) is not None


def redact_text(value: str) -> tuple[str, bool]:
    """脱敏自由文本，返回 (脱敏后文本, 是否发生变化)。

    幂等：`***REDACTED***` 本身不含任何敏感模式，重复调用结果稳定。
    """
    cleaned = _SENSITIVE_TEXT_PATTERN.sub(_replace_match, value)
    return cleaned, cleaned != value


def _replace_match(match: re.Match[str]) -> str:
    """保留检出位置的语义前缀，只把凭证本体替换为占位符。"""
    group = match.lastgroup or ""
    text = match.group(0)

    if group == "url":
        # 整条 URL 替换（截图 / 视频链接可能指向人脸或车牌原图）。
        return REDACTED_VALUE
    if group == "bearer":
        scheme = text.split(None, 1)[0]
        return f"{scheme} {REDACTED_VALUE}"
    if group in {"kv", "identifier_kv"}:
        separator_index = min(
            (index for index in (text.find(":"), text.find("=")) if index >= 0),
            default=-1,
        )
        if separator_index >= 0:
            return f"{text[: separator_index + 1]} {REDACTED_VALUE}"
        return REDACTED_VALUE
    if group == "url_param":
        separator_index = text.find("=")
        return f"{text[: separator_index + 1]}{REDACTED_VALUE}"
    return REDACTED_VALUE


def redact_value(value: Any) -> tuple[Any, bool]:
    """递归脱敏 string / list / dict，键名命中敏感模式时整体替换。"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            cleaned, item_changed = redact_value(item)
            items.append(cleaned)
            changed = changed or item_changed
        return items, changed
    if isinstance(value, tuple):
        cleaned, changed = redact_value(list(value))
        return tuple(cleaned), changed
    if isinstance(value, dict):
        return redact_mapping(value)
    return value, False


def redact_mapping(values: dict[Any, Any]) -> tuple[dict[Any, Any], bool]:
    """递归脱敏映射：敏感键整体替换，其余值递归处理。"""
    redacted: dict[Any, Any] = {}
    changed = False
    for key, value in values.items():
        if value in (None, ""):
            redacted[key] = value
        elif is_sensitive_key(str(key)):
            redacted[key] = REDACTED_VALUE
            changed = True
        else:
            cleaned, item_changed = redact_value(value)
            redacted[key] = cleaned
            changed = changed or item_changed
    return redacted, changed


__all__ = [
    "REDACTED_VALUE",
    "is_sensitive_key",
    "redact_mapping",
    "redact_text",
    "redact_value",
]
