from scripts.demo_phase5_knowledge_loop import run_demo

from security_diagnosis_harness.adapters.embedding.fake import FakeEmbeddingAdapter


def test_phase5_demo_closes_diagnosis_to_retrieval_loop_offline():
    result = run_demo(FakeEmbeddingAdapter())

    assert result["source_diagnosis_status"] == "confirmed"
    assert result["knowledge_status"] == "confirmed"
    assert result["retrieval_mode"] == "hybrid"
    assert result["semantic_fallback"] is False
    assert result["recalled_count"] == 1
    assert result["knowledge_id"] in result["recalled_knowledge_ids"]
    assert result["evidence_type"] == "knowledge_sop"
