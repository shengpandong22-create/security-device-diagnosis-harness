"""Phase 5C 检索评测脚本离线验收。"""

from scripts.eval_phase5_knowledge_retrieval import evaluate

from security_diagnosis_harness.adapters.embedding.fake import FakeEmbeddingAdapter


def test_retrieval_eval_compares_three_modes_without_external_service():
    report = evaluate(embedding=FakeEmbeddingAdapter())

    assert report["total"] == 12
    assert report["bge_cold_start_ms"] >= 0.0
    assert set(report["modes"]) == {"keyword", "vector", "hybrid"}
    for metrics in report["modes"].values():
        assert 0.0 <= metrics["recall_at_1"] <= 1.0
        assert 0.0 <= metrics["recall_at_3"] <= 1.0
        assert 0.0 <= metrics["mrr"] <= 1.0
        assert metrics["average_elapsed_ms"] >= 0.0
