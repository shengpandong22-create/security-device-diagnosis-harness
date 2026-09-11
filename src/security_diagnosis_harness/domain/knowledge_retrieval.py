"""知识检索共用的确定性词法规则。"""

from __future__ import annotations

import re

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")
MIN_LEXICAL_OVERLAP = 2


def lexical_terms(text: str) -> set[str]:
    """提取英数字词项与中文二元组，避免把整句中文当作一个关键词。"""
    terms: set[str] = set()
    for token in _TOKEN_PATTERN.findall(text.lower()):
        if token.isascii():
            terms.add(token)
            continue
        if len(token) == 1:
            terms.add(token)
            continue
        terms.update(token[index : index + 2] for index in range(len(token) - 1))
    return terms


def lexical_overlap_score(query: str, document: str) -> int:
    """返回查询与文档之间的词法交集数量。"""
    return len(lexical_terms(query) & lexical_terms(document))
