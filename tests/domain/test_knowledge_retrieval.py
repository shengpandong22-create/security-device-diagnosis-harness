from security_diagnosis_harness.domain.knowledge_retrieval import (
    lexical_overlap_score,
    lexical_terms,
)


def test_chinese_sentence_is_split_into_bigrams():
    assert {"设备", "备离", "离线"}.issubset(lexical_terms("设备离线"))


def test_ascii_error_code_remains_exact_term():
    assert "stream_publish_failed" in lexical_terms("STREAM_PUBLISH_FAILED 摄像头")


def test_lexical_overlap_handles_chinese_sentences_without_spaces():
    assert lexical_overlap_score("设备完全没反应", "控制器离线导致设备没有反应") > 0
