"""Embedding adapter contract tests.

注意：所有 HTTP 测试都用 ``httpx.MockTransport``，**不访问真实 BGE 服务**。
"""

import json
from pathlib import Path

import httpx
import pytest

from security_diagnosis_harness.adapters.embedding import (
    BgeEmbeddingConfigurationError,
    BgeEmbeddingError,
    FakeEmbeddingAdapter,
    HttpBgeEmbeddingAdapter,
)

BGE_BASE_URL = "http://127.0.0.1:18080"


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url=BGE_BASE_URL
    )


def _adapter(client: httpx.Client, **changes) -> HttpBgeEmbeddingAdapter:
    values = {"base_url": BGE_BASE_URL, "model": "test-bge", "dimension": 3}
    values.update(changes)
    values["client"] = client
    return HttpBgeEmbeddingAdapter(**values)


def test_fake_embedding_is_deterministic_and_normalized():
    adapter = FakeEmbeddingAdapter(dimension=16)
    first = adapter.embed_query("摄像头黑屏")
    second = adapter.embed_query("摄像头黑屏")
    assert first == second
    assert len(first) == 16
    assert sum(value * value for value in first) == pytest.approx(1.0)


def test_http_bge_adapter_uses_openai_style_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/embeddings"
        assert body == {"model": "test-bge", "input": ["a", "b"]}
        return httpx.Response(
            200,
            json={
                "model": "test-bge",
                "dimension": 3,
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                ],
            },
        )

    adapter = _adapter(_client(handler))

    assert adapter.embed_documents(["a", "b"]) == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "wrong", "dimension": 3, "data": []},
        {"model": "test-bge", "dimension": 2, "data": []},
        {
            "model": "test-bge",
            "dimension": 3,
            "data": [{"index": 0, "embedding": [1.0, 0.0]}],
        },
    ],
)
def test_http_bge_adapter_rejects_invalid_service_payload(payload):
    adapter = _adapter(_client(lambda _: httpx.Response(200, json=payload)))
    with pytest.raises(RuntimeError):
        adapter.embed_query("query")


# ---------------------------------------------------------------- endpoint allowlist
@pytest.mark.parametrize(
    "base_url",
    [
        "http://bge.remote.example:18080",
        "http://10.0.0.5:18080",
        "ftp://127.0.0.1:18080",
        "http://127.0.0.1:9999",
        "http://user:password@127.0.0.1:18080",
        "http://127.0.0.1:18080?api_key=secret",
        "http://127.0.0.1:18080#fragment",
        "http://127.0.0.1:abc",
    ],
)
def test_bge_endpoint_must_be_allowlisted(base_url):
    with pytest.raises(BgeEmbeddingConfigurationError):
        HttpBgeEmbeddingAdapter(base_url=base_url, model="test-bge", dimension=3)


def test_bge_endpoint_configuration_errors_never_echo_the_url():
    with pytest.raises(BgeEmbeddingConfigurationError) as excinfo:
        HttpBgeEmbeddingAdapter(
            base_url="http://secret-host.example:18080", model="test-bge", dimension=3
        )
    assert "secret-host.example" not in str(excinfo.value)


def test_bge_endpoint_allowlist_can_be_extended_explicitly():
    adapter = HttpBgeEmbeddingAdapter(
        base_url="https://bge.internal:8443/v1",
        model="test-bge",
        dimension=3,
        allowed_hosts=frozenset({"bge.internal"}),
        allowed_ports=frozenset({8443}),
        client=_client(lambda _: httpx.Response(200, json={})),
    )
    assert adapter.dimension == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"dimension": 0},
        {"timeout_seconds": 0},
        {"max_batch_size": 0},
        {"max_text_chars": 0},
        {"max_total_chars": 0},
        {"max_response_bytes": 0},
    ],
)
def test_bge_invalid_bounds_are_rejected(changes):
    values = {"base_url": BGE_BASE_URL, "model": "test-bge", "dimension": 3}
    values.update(changes)
    with pytest.raises(BgeEmbeddingConfigurationError):
        HttpBgeEmbeddingAdapter(**values)


# ---------------------------------------------------------------- input bounds
def _counting_handler(payload, calls: list[int]):
    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=payload)

    return handler


def test_bge_rejects_oversized_batch_before_any_request():
    calls: list[int] = []
    adapter = _adapter(
        _client(_counting_handler({}, calls)), max_batch_size=2
    )
    with pytest.raises(BgeEmbeddingError) as excinfo:
        adapter.embed_documents(["a", "b", "c"])
    assert excinfo.value.error_type == "batch_too_large"
    assert calls == []


def test_bge_rejects_oversized_single_text_before_any_request():
    calls: list[int] = []
    adapter = _adapter(_client(_counting_handler({}, calls)), max_text_chars=3)
    with pytest.raises(BgeEmbeddingError) as excinfo:
        adapter.embed_documents(["abcd"])
    assert excinfo.value.error_type == "text_too_large"
    assert calls == []


def test_bge_rejects_oversized_total_text_before_any_request():
    calls: list[int] = []
    adapter = _adapter(_client(_counting_handler({}, calls)), max_total_chars=5)
    with pytest.raises(BgeEmbeddingError) as excinfo:
        adapter.embed_documents(["abc", "def"])
    assert excinfo.value.error_type == "total_text_too_large"
    assert calls == []


def test_bge_response_body_size_is_bounded():
    secret = "provider-secret-body"
    adapter = _adapter(
        _client(lambda _: httpx.Response(200, text=("x" * 4_000) + secret)),
        max_response_bytes=1_000,
    )
    with pytest.raises(BgeEmbeddingError) as excinfo:
        adapter.embed_query("query")
    assert excinfo.value.error_type == "response_too_large"
    assert secret not in str(excinfo.value)
    assert "127.0.0.1" not in str(excinfo.value)


# ---------------------------------------------------------------- vector validation
def _vector_payload(data) -> dict:
    return {"model": "test-bge", "dimension": 3, "data": data}


@pytest.mark.parametrize(
    ("data", "inputs", "error_type"),
    [
        ([{"index": 0, "embedding": [1.0, float("nan"), 0.0]}], 1, "non_finite_vector"),
        ([{"index": 0, "embedding": [1.0, float("inf"), 0.0]}], 1, "non_finite_vector"),
        ([{"index": 0, "embedding": [1.0, "0.0", 0.0]}], 1, "invalid_vector"),
        ([{"index": 0, "embedding": [1.0, True, 0.0]}], 1, "invalid_vector"),
        ([{"index": 0, "embedding": [1.0, 0.0]}], 1, "invalid_vector"),
        ([{"index": -1, "embedding": [1.0, 0.0, 0.0]}], 1, "invalid_index"),
        ([{"index": 3, "embedding": [1.0, 0.0, 0.0]}], 1, "invalid_index"),
        ([{"index": "0", "embedding": [1.0, 0.0, 0.0]}], 1, "invalid_index"),
        ([{"index": True, "embedding": [1.0, 0.0, 0.0]}], 1, "invalid_index"),
        (
            [
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                {"index": 0, "embedding": [0.0, 1.0, 0.0]},
            ],
            2,
            "duplicate_index",
        ),
        (
            [
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                {"index": 2, "embedding": [0.0, 0.0, 1.0]},
            ],
            2,
            "invalid_index",
        ),
        (
            [
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                {"index": 1, "embedding": [0.0, 1.0, 0.0]},
            ],
            3,
            "invalid_vector_count",
        ),
        ([{"embedding": [1.0, 0.0, 0.0]}], 1, "invalid_index"),
        ([{"index": 0, "embedding": [1.0, 0.0, 0.0]}, "not-a-mapping"], 2, "invalid_vector"),
    ],
)
def test_bge_rejects_illegal_vectors_and_indices(data, inputs, error_type):
    # 用 text=（而非 json=）构造响应体：httpx 的 json= 会拒绝 NaN/Infinity，
    # 而这里正是要模拟"非严格 JSON 服务端返回 NaN/Infinity"的越界场景。
    body = json.dumps(_vector_payload(data), allow_nan=True)
    adapter = _adapter(_client(lambda _: httpx.Response(200, text=body)))
    with pytest.raises(BgeEmbeddingError) as excinfo:
        adapter.embed_documents(["t"] * inputs)
    assert excinfo.value.error_type == error_type


def test_bge_accepts_out_of_order_indices():
    adapter = _adapter(
        _client(
            lambda _: httpx.Response(
                200,
                json=_vector_payload(
                    [
                        {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                        {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    ]
                ),
            )
        )
    )
    assert adapter.embed_documents(["a", "b"]) == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]


def test_bge_transport_error_is_stable_and_hides_endpoint():
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 127.0.0.1:18080")

    adapter = _adapter(_client(handler))
    with pytest.raises(BgeEmbeddingError) as excinfo:
        adapter.embed_query("query")
    assert excinfo.value.error_type == "transport_error"
    assert "127.0.0.1" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_bge_context_manager_closes_owned_client():
    adapter = HttpBgeEmbeddingAdapter(
        base_url=BGE_BASE_URL, model="test-bge", dimension=3
    )
    with adapter as entered:
        assert entered is adapter
    # close() 幂等；自建 client 已被关闭（未访问任何真实服务）。
    adapter.close()


def test_bge_does_not_close_injected_client():
    client = _client(lambda _: httpx.Response(200, json={}))
    adapter = _adapter(client)
    adapter.close()
    assert client.is_closed is False
    client.close()


def test_bge_adapter_is_not_wired_into_the_formal_runtime():
    from security_diagnosis_harness import runtime as runtime_module

    assert not hasattr(runtime_module, "HttpBgeEmbeddingAdapter")
    text = Path(runtime_module.__file__).read_text(encoding="utf-8")
    assert "http_bge" not in text
    assert "BgeEmbedding" not in text
