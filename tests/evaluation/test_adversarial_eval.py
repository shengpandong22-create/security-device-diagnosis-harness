from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.eval_adversarial_cases import evaluate

from security_diagnosis_harness.evaluation.adversarial import (
    AdversarialFixture,
    AdversarialOutcome,
    TrapType,
    classify_outcome,
    load_adversarial_fixture,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "evaluation-fixtures/adversarial-development-1.0.0.json"


def test_adversarial_fixture_covers_all_domains_and_traps():
    fixture = load_adversarial_fixture(FIXTURE)

    assert fixture.report_kind == "adversarial_eval"
    assert len(fixture.cases) == 8
    assert set(case.trap_type for case in fixture.cases) == set(TrapType)
    assert all(
        sum(item.fault_type is case.fault_type for item in fixture.cases) >= 2
        for case in fixture.cases
    )


def test_adversarial_fixture_rejects_missing_trap_coverage():
    fixture = load_adversarial_fixture(FIXTURE)
    payload = fixture.model_dump(mode="json")
    payload["cases"] = [
        item
        for item in payload["cases"]
        if item["trap_type"] != TrapType.SOP_ONLY_OR_WEAK_EVIDENCE.value
    ]

    with pytest.raises(ValidationError, match="至少需要 8 个案例|覆盖全部 trap_type"):
        AdversarialFixture.model_validate(payload)


@pytest.mark.parametrize(
    ("passed", "degraded", "detected", "expected"),
    [
        (True, False, True, AdversarialOutcome.SYSTEM_CORRECT_GATE_CORRECT),
        (True, True, True, AdversarialOutcome.SAFE_DEGRADATION_CORRECT),
        (False, False, True, AdversarialOutcome.SYSTEM_ERROR_CAUGHT_BY_EVAL),
        (False, False, False, AdversarialOutcome.SYSTEM_ERROR_MISSED_BY_EVAL),
    ],
)
def test_outcome_classification(passed, degraded, detected, expected):
    assert (
        classify_outcome(
            oracle_passed=passed,
            expected_degradation=degraded,
            eval_detected_error=detected,
        )
        is expected
    )


def test_adversarial_semantic_result_is_stable_across_runs():
    fixed_time = datetime(2026, 10, 23, tzinfo=UTC)
    first = evaluate(FIXTURE, code_commit="a" * 40, run_at=fixed_time)
    second = evaluate(FIXTURE, code_commit="b" * 40, run_at=fixed_time)

    assert first.semantic_hash() == second.semantic_hash()
    assert first.semantic_payload() == second.semantic_payload()
    assert first.outcome_counts == {
        AdversarialOutcome.SYSTEM_CORRECT_GATE_CORRECT: 4,
        AdversarialOutcome.SYSTEM_ERROR_CAUGHT_BY_EVAL: 0,
        AdversarialOutcome.SYSTEM_ERROR_MISSED_BY_EVAL: 0,
        AdversarialOutcome.SAFE_DEGRADATION_CORRECT: 4,
    }
