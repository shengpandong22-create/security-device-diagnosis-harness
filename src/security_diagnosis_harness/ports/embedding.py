"""文本向量化 Port。"""

from typing import Protocol


class EmbeddingPort(Protocol):
    """业务侧只依赖向量能力，不依赖具体模型运行方式。"""

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
