"""受控 HTTP 响应读取：在**传输过程中**限制响应体大小。

为什么必须流式：``httpx.Client.post()`` 返回之前通常已经把整个响应体读进内存。此时
再做 ``len(response.content) > limit`` 只能阻止 JSON 解析，不能阻止超大响应被完整
下载、进入内存、耗尽客户端资源。真正的资源边界必须在读取过程中生效：

1. 用 ``client.stream(...)`` 打开响应，此时 body 尚未读取；
2. 用 ``response.iter_bytes()`` 逐块读取；
3. 累计实际字节数，一旦超过上限立即停止读取并关闭响应；
4. 只有完整响应不超过上限时才交给调用方解析。

本模块只依赖 ``httpx``，被真实模型评测客户端与 BGE 适配器共同复用，避免两处各自
实现出不同语义。
"""

from __future__ import annotations

from time import monotonic

import httpx

__all__ = ["ResponseDeadlineExceeded", "ResponseTooLargeError", "read_bounded_bytes"]


class ResponseDeadlineExceeded(Exception):
    """整次响应读取超过调用方给出的绝对墙钟 deadline。"""


class ResponseTooLargeError(Exception):
    """响应体超过调用方声明的字节上限。

    抛出前已经停止读取并关闭响应，因此不会继续消耗网络与内存。异常文本只包含上限
    数值，不携带 endpoint、请求头、正文或凭证。
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"响应体超过上限 {max_bytes} 字节")


def read_bounded_bytes(
    response: httpx.Response, max_bytes: int, *, deadline: float | None = None
) -> bytes:
    """逐块读取响应体，累计超过 ``max_bytes`` 时立刻停止并关闭响应。

    读取过程中一旦超限就抛出 :class:`ResponseTooLargeError`，**不会**继续读取剩余
    数据块，因此后续块不会被下载、也不会进入内存。

    Raises:
        ValueError: ``max_bytes`` 不是正整数。
        ResponseTooLargeError: 累计字节数超过上限（响应已关闭）。
    """
    if max_bytes < 1:
        raise ValueError("max_bytes 必须为正整数")
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_bytes():
            if deadline is not None and monotonic() > deadline:
                raise ResponseDeadlineExceeded
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLargeError(max_bytes)
            chunks.append(chunk)
        if deadline is not None and monotonic() > deadline:
            raise ResponseDeadlineExceeded
    except BaseException:
        # 立即释放连接：超限时停止读取，传输异常时避免连接泄漏。
        response.close()
        raise
    return b"".join(chunks)
