"""独立 BGE HTTP 服务适配器。"""

from __future__ import annotations

import httpx


class HttpBgeEmbeddingAdapter:
    """调用 OpenAI 风格 `/v1/embeddings`，并严格校验维度。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18080",
        model: str = "BAAI/bge-small-zh-v1.5",
        dimension: int = 512,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._dimension = dimension
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.post(
            "/v1/embeddings", json={"model": self._model, "input": texts}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("model") != self._model or payload.get("dimension") != self._dimension:
            raise RuntimeError("BGE 服务返回的模型或维度不匹配")
        data = sorted(payload.get("data", []), key=lambda item: item.get("index", -1))
        vectors = [item.get("embedding") for item in data]
        if len(vectors) != len(texts) or any(
            not isinstance(vector, list) or len(vector) != self._dimension
            for vector in vectors
        ):
            raise RuntimeError("BGE 服务返回了非法向量")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
