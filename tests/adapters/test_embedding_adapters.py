"""Embedding adapter contract tests."""

import json

import httpx
import pytest

from security_diagnosis_harness.adapters.embedding.fake import FakeEmbeddingAdapter
from security_diagnosis_harness.adapters.embedding.http_bge import HttpBgeEmbeddingAdapter


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

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://bge.test"
    )
    adapter = HttpBgeEmbeddingAdapter(
        base_url="http://bge.test", model="test-bge", dimension=3, client=client
    )

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
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        base_url="http://bge.test",
    )
    adapter = HttpBgeEmbeddingAdapter(
        base_url="http://bge.test", model="test-bge", dimension=3, client=client
    )
    with pytest.raises(RuntimeError):
        adapter.embed_query("query")
