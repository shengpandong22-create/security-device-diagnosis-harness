"""Embedding adapters."""

from security_diagnosis_harness.adapters.embedding.fake import FakeEmbeddingAdapter
from security_diagnosis_harness.adapters.embedding.http_bge import (
    BgeEmbeddingConfigurationError,
    BgeEmbeddingError,
    HttpBgeEmbeddingAdapter,
)

__all__ = [
    "BgeEmbeddingConfigurationError",
    "BgeEmbeddingError",
    "FakeEmbeddingAdapter",
    "HttpBgeEmbeddingAdapter",
]
