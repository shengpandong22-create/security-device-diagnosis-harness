from __future__ import annotations

from pathlib import Path

import pytest

from security_diagnosis_harness.evaluation import (
    DatasetAdmissionError,
    DatasetAdmissionReview,
    DatasetRegistry,
    DatasetSplit,
    ReviewDecision,
    validate_dataset_admission,
)

DATASET_ROOT = (
    Path(__file__).resolve().parents[2] / "datasets" / "security-diagnosis" / "1.0.0"
)


@pytest.fixture(scope="module")
def registry():
    return DatasetRegistry.load(DATASET_ROOT)


def _proposed_case(registry):
    base = registry.cases(DatasetSplit.VALIDATION)[0]
    return base.model_copy(
        update={
            "case_id": "validation-new-controller-power-loss",
            "template_group_id": "access-controller-power-new",
            "source_record_id": "human-reviewed-new-001",
            "input_facts": {
                "device_id": "new-controller-01",
                "controller_online": False,
                "power_indicator": "off",
                "network_probe": "unreachable",
            },
            "expected_candidate": "controller_offline_or_no_response",
        }
    )


def _review(registry, decision=ReviewDecision.APPROVE):
    return DatasetAdmissionReview(
        proposed_case=_proposed_case(registry),
        decision=decision,
        reviewer="security-domain-reviewer",
        rationale="证据与标签经过人工复核",
    )


def test_approved_unique_case_can_become_candidate(registry):
    proposed = validate_dataset_admission(_review(registry), registry)
    assert proposed.case_id == "validation-new-controller-power-loss"
    # 准入只返回候选，不自动写入正式数据集。
    assert registry.case_count(DatasetSplit.VALIDATION) == 2


@pytest.mark.parametrize("decision", [ReviewDecision.REJECT, ReviewDecision.NEEDS_REVISION])
def test_non_approved_case_cannot_enter_dataset(registry, decision):
    with pytest.raises(DatasetAdmissionError, match="人工 approve"):
        validate_dataset_admission(_review(registry, decision), registry)


def test_approved_but_duplicate_case_is_rejected(registry):
    existing = registry.cases(DatasetSplit.VALIDATION)[0]
    review = DatasetAdmissionReview(
        proposed_case=existing,
        decision=ReviewDecision.APPROVE,
        reviewer="reviewer",
        rationale="duplicate",
    )
    with pytest.raises(DatasetAdmissionError, match="隔离检查"):
        validate_dataset_admission(review, registry)


def test_review_text_is_redacted(registry):
    review = DatasetAdmissionReview(
        proposed_case=_proposed_case(registry),
        decision=ReviewDecision.APPROVE,
        reviewer="token=reviewer-secret",
        rationale="password=plain-secret-value",
    )
    assert "reviewer-secret" not in review.reviewer
    assert "plain-secret-value" not in review.rationale


def test_approval_cannot_bypass_post_construction_sensitive_mutation(registry):
    proposed = _proposed_case(registry)
    proposed.input_facts["password"] = "late-injected-secret"
    # model_construct 模拟不可信反序列化/内部旁路，准入函数仍必须二次复验。
    review = DatasetAdmissionReview.model_construct(
        proposed_case=proposed,
        decision=ReviewDecision.APPROVE,
        reviewer="reviewer",
        rationale="approved",
    )
    with pytest.raises(DatasetAdmissionError, match="安全协议"):
        validate_dataset_admission(review, registry)
