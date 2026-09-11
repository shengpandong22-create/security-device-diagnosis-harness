"""测试用确定性向量适配器。"""

from __future__ import annotations

import hashlib
import math


class FakeEmbeddingAdapter:
    """基于字符 n-gram 哈希生成稳定向量，不访问网络。"""

    def __init__(self, dimension: int = 64) -> None:
        self._dimension = dimension
        self.call_count = 0

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def _embed(self, text: str) -> list[float]:
        normalized = "".join(text.lower().split())
        vector = [0.0] * self._dimension
        tokens = [normalized[index : index + 2] for index in range(max(1, len(normalized) - 1))]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
