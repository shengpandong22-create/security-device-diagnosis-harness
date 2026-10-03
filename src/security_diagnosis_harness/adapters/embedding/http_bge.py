"""独立 BGE HTTP 服务适配器（带 endpoint 与资源边界）。

设计约束：

- 只对接显式允许的 BGE endpoint：**host / scheme / port 三重 allowlist**，并禁止
  userinfo、query 与 fragment；默认只允许本机 ``http://127.0.0.1:18080``；
- 严格的输入与响应边界：批量条数、单条字符数、总字符数，以及**传输期**响应字节
  上限（``client.stream`` + ``iter_bytes``，逐块累计，超限立刻停止读取并关闭响应）；
- 向量必须是**有限数值**、长度等于声明维度；``index`` 必须恰好覆盖 ``0..n-1``
  （拒绝越界、重复、缺失与数量不符）；
- 稳定错误类型（:class:`BgeEmbeddingError`），不携带 endpoint、响应正文或向量内容；
- 支持 ``close()`` 与上下文管理器释放自建 ``httpx.Client``；
- **不接入正式 Runtime**：本适配器只服务离线演示 / 知识检索脚本，Runtime 不引用它。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from time import monotonic
from types import TracebackType
from typing import Any
from urllib.parse import urlparse

import httpx

from security_diagnosis_harness.http_stream import (
    ResponseDeadlineExceeded,
    ResponseTooLargeError,
    read_bounded_bytes,
)

DEFAULT_BGE_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_BGE_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_BGE_DIMENSION = 512
DEFAULT_BGE_TIMEOUT_SECONDS = 10.0

#: 默认只允许本机地址；跨机部署必须显式扩展 allowlist。
DEFAULT_BGE_ALLOWED_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_BGE_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
#: 默认只允许 BGE 服务默认端口；其它端口必须显式加入 allowlist。
DEFAULT_BGE_ALLOWED_PORTS: frozenset[int] = frozenset({18080})

DEFAULT_BGE_MAX_BATCH_SIZE = 64
DEFAULT_BGE_MAX_TEXT_CHARS = 8_192
DEFAULT_BGE_MAX_TOTAL_CHARS = 65_536
DEFAULT_BGE_MAX_RESPONSE_BYTES = 8 * 1_048_576


class BgeEmbeddingConfigurationError(ValueError):
    """BGE 适配器配置非法（endpoint 或边界参数）。异常文本不含具体 endpoint。"""


class BgeEmbeddingError(RuntimeError):
    """BGE 调用受控失败。

    ``error_type`` 是稳定的错误分类码；异常文本与因果链都不携带 endpoint、
    响应正文或向量内容，可安全向上传递与记录。
    """

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(f"BGE 嵌入调用失败: {error_type}")


def _validate_base_url(
    base_url: str,
    allowed_hosts: frozenset[str],
    allowed_schemes: frozenset[str],
    allowed_ports: frozenset[int],
) -> str:
    """校验 endpoint：scheme / host / port allowlist，且禁止 userinfo/query/fragment。"""
    parsed = urlparse(base_url)
    if parsed.scheme not in allowed_schemes:
        raise BgeEmbeddingConfigurationError("BGE base_url 的 scheme 不在 allowlist 内")
    if parsed.username or parsed.password:
        raise BgeEmbeddingConfigurationError("BGE base_url 不得包含 userinfo")
    if parsed.query or parsed.fragment:
        raise BgeEmbeddingConfigurationError("BGE base_url 不得包含 query 或 fragment")
    host = parsed.hostname
    if host is None or host not in allowed_hosts:
        raise BgeEmbeddingConfigurationError("BGE base_url 的 host 不在 allowlist 内")
    try:
        port = parsed.port
    except ValueError:
        raise BgeEmbeddingConfigurationError("BGE base_url 的 port 非法") from None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if port not in allowed_ports:
        raise BgeEmbeddingConfigurationError("BGE base_url 的 port 不在 allowlist 内")
    return base_url.rstrip("/")


class HttpBgeEmbeddingAdapter:
    """调用 OpenAI 风格 `/v1/embeddings`，并严格校验 endpoint、输入与向量。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BGE_BASE_URL,
        model: str = DEFAULT_BGE_MODEL,
        dimension: int = DEFAULT_BGE_DIMENSION,
        timeout_seconds: float = DEFAULT_BGE_TIMEOUT_SECONDS,
        *,
        allowed_hosts: frozenset[str] = DEFAULT_BGE_ALLOWED_HOSTS,
        allowed_schemes: frozenset[str] = DEFAULT_BGE_ALLOWED_SCHEMES,
        allowed_ports: frozenset[int] = DEFAULT_BGE_ALLOWED_PORTS,
        max_batch_size: int = DEFAULT_BGE_MAX_BATCH_SIZE,
        max_text_chars: int = DEFAULT_BGE_MAX_TEXT_CHARS,
        max_total_chars: int = DEFAULT_BGE_MAX_TOTAL_CHARS,
        max_response_bytes: int = DEFAULT_BGE_MAX_RESPONSE_BYTES,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = _validate_base_url(
            base_url, allowed_hosts, allowed_schemes, allowed_ports
        )
        if not model.strip():
            raise BgeEmbeddingConfigurationError("BGE 模型名不能为空")
        if dimension < 1:
            raise BgeEmbeddingConfigurationError("BGE 维度必须为正整数")
        if not 0 < timeout_seconds <= 300:
            raise BgeEmbeddingConfigurationError("BGE 超时必须大于 0 且不超过 300 秒")
        for name, value in (
            ("max_batch_size", max_batch_size),
            ("max_text_chars", max_text_chars),
            ("max_total_chars", max_total_chars),
            ("max_response_bytes", max_response_bytes),
        ):
            if value < 1:
                raise BgeEmbeddingConfigurationError(f"{name} 必须为正整数")
        self._model = model
        self._dimension = dimension
        self._max_batch_size = max_batch_size
        self._max_text_chars = max_text_chars
        self._max_total_chars = max_total_chars
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self._base_url, timeout=timeout_seconds
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list):
            raise BgeEmbeddingError("invalid_input")
        if not texts:
            return []
        self._validate_texts(texts)
        deadline = monotonic() + self._timeout_seconds
        try:
            with self._client.stream(
                "POST", "/v1/embeddings", json={"model": self._model, "input": texts}
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise BgeEmbeddingError(
                        f"http_{exc.response.status_code}"
                    ) from None
                # 传输期资源边界：逐块读取，超限立刻停止并关闭响应。
                try:
                    body = read_bounded_bytes(
                        response,
                        self._max_response_bytes,
                        deadline=deadline,
                    )
                except ResponseTooLargeError:
                    raise BgeEmbeddingError("response_too_large") from None
                except ResponseDeadlineExceeded:
                    raise BgeEmbeddingError("timeout") from None
        except httpx.HTTPError:
            # 不链式保留原始异常：httpx 异常文本包含请求 endpoint。
            raise BgeEmbeddingError("transport_error") from None
        try:
            payload = json.loads(body)
        except ValueError:
            raise BgeEmbeddingError("invalid_response") from None
        return self._parse_embeddings(payload, len(texts))

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpBgeEmbeddingAdapter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------ 输入 / 响应校验
    def _validate_texts(self, texts: list[str]) -> None:
        if len(texts) > self._max_batch_size:
            raise BgeEmbeddingError("batch_too_large")
        total_chars = 0
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise BgeEmbeddingError("invalid_text")
            if len(text) > self._max_text_chars:
                raise BgeEmbeddingError("text_too_large")
            total_chars += len(text)
        if total_chars > self._max_total_chars:
            raise BgeEmbeddingError("total_text_too_large")

    def _parse_embeddings(self, payload: Any, expected: int) -> list[list[float]]:
        if not isinstance(payload, Mapping):
            raise BgeEmbeddingError("invalid_response")
        if (
            payload.get("model") != self._model
            or payload.get("dimension") != self._dimension
        ):
            raise BgeEmbeddingError("service_mismatch")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise BgeEmbeddingError("invalid_vector_count")
        vectors: list[list[float] | None] = [None] * expected
        for item in data:
            if not isinstance(item, Mapping):
                raise BgeEmbeddingError("invalid_vector")
            index = item.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise BgeEmbeddingError("invalid_index")
            if not 0 <= index < expected:
                raise BgeEmbeddingError("invalid_index")
            if vectors[index] is not None:
                raise BgeEmbeddingError("duplicate_index")
            vectors[index] = _require_vector(item.get("embedding"), self._dimension)
        ordered: list[list[float]] = []
        for vector in vectors:
            if vector is None:
                raise BgeEmbeddingError("invalid_index")
            ordered.append(vector)
        return ordered


def _require_vector(value: Any, dimension: int) -> list[float]:
    """校验单条向量：必须是定长列表、元素为有限数值（拒绝 str / bool / NaN / Inf）。"""
    if not isinstance(value, list) or len(value) != dimension:
        raise BgeEmbeddingError("invalid_vector")
    resolved: list[float] = []
    for element in value:
        if isinstance(element, bool) or not isinstance(element, (int, float)):
            raise BgeEmbeddingError("invalid_vector")
        number = float(element)
        if not math.isfinite(number):
            raise BgeEmbeddingError("non_finite_vector")
        resolved.append(number)
    return resolved


__all__ = [
    "DEFAULT_BGE_ALLOWED_HOSTS",
    "DEFAULT_BGE_ALLOWED_PORTS",
    "DEFAULT_BGE_ALLOWED_SCHEMES",
    "DEFAULT_BGE_BASE_URL",
    "DEFAULT_BGE_DIMENSION",
    "DEFAULT_BGE_MAX_BATCH_SIZE",
    "DEFAULT_BGE_MAX_RESPONSE_BYTES",
    "DEFAULT_BGE_MAX_TEXT_CHARS",
    "DEFAULT_BGE_MAX_TOTAL_CHARS",
    "DEFAULT_BGE_MODEL",
    "BgeEmbeddingConfigurationError",
    "BgeEmbeddingError",
    "HttpBgeEmbeddingAdapter",
]
