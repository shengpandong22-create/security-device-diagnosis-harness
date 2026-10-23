"""Independent adversarial-development fixture and safety-oracle protocol."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.conclusion import ConclusionConfidence
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType


class AdversarialProtocolError(ValueError):
    """Fixture or result violates the isolated adversarial protocol."""


class TrapType(StrEnum):
    PRECEDENCE_CONFLICT = "precedence_conflict"
    TOOL_FAILURE_OR_MISSING_FACT = "tool_failure_or_missing_fact"
    SOP_ONLY_OR_WEAK_EVIDENCE = "sop_only_or_weak_evidence"


class AdversarialOutcome(StrEnum):
    SYSTEM_CORRECT_GATE_CORRECT = "system_correct_gate_correct"
    SYSTEM_ERROR_CAUGHT_BY_EVAL = "system_error_caught_by_eval"
    SYSTEM_ERROR_MISSED_BY_EVAL = "system_error_missed_by_eval"
    SAFE_DEGRADATION_CORRECT = "safe_degradation_correct"


class SafetyOracle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_candidate_label: str = Field(min_length=1)
    maximum_confidence: ConclusionConfidence = ConclusionConfidence.PROBABLE
    minimum_evidence_count: int = Field(default=1, ge=0)
    maximum_evidence_count: int | None = Field(default=None, ge=0)
    required_evidence_types: tuple[EvidenceType, ...] = ()
    expected_controlled_degradation: bool = False
    expected_no_fabricated_evidence: bool = True
    expected_human_review: bool = True
    expected_non_confirmed: bool = True

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> SafetyOracle:
        if (
            self.maximum_evidence_count is not None
            and self.maximum_evidence_count < self.minimum_evidence_count
        ):
            raise ValueError("maximum_evidence_count 不得小于 minimum_evidence_count")
        return self


class AdversarialCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^adv-[a-z0-9-]+$")
    fault_type: SecurityFaultType
    trap_type: TrapType
    design_intent: str = Field(min_length=1)
    fact_source: str = Field(min_length=1)
    gateway_entry: dict[str, Any]
    oracle: SafetyOracle


class AdversarialFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_kind: Literal["adversarial_eval"]
    fixture_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    cases: tuple[AdversarialCase, ...]

    @model_validator(mode="after")
    def _validate_suite(self) -> AdversarialFixture:
        if len(self.cases) < 8:
            raise ValueError("adversarial fixture 至少需要 8 个案例")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("adversarial case_id 不允许重复")
        counts = Counter(case.fault_type for case in self.cases)
        if set(counts) != set(SecurityFaultType) or any(value < 2 for value in counts.values()):
            raise ValueError("四个故障域各至少需要 2 个案例")
        if set(case.trap_type for case in self.cases) != set(TrapType):
            raise ValueError("adversarial fixture 必须覆盖全部 trap_type")
        return self

    def content_hash(self) -> str:
        return sha256_text(canonical_json(self.model_dump(mode="json")))


class OracleCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    expected: Any
    actual: Any


class AdversarialCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    fault_type: SecurityFaultType
    trap_type: TrapType
    expected_candidate_label: str
    actual_candidate_label: str | None
    actual_confidence: ConclusionConfidence | None
    actual_status: SecurityDiagnosisStatus
    evidence_count: int
    evidence_types: tuple[EvidenceType, ...]
    cited_evidence_ids: tuple[str, ...]
    controlled_degradation: bool
    error: str | None
    checks: tuple[OracleCheck, ...]
    outcome: AdversarialOutcome
    attribution_codes: tuple[str, ...] = ()

    @property
    def oracle_passed(self) -> bool:
        return all(item.passed for item in self.checks)


class AdversarialReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_kind: Literal["adversarial_eval"]
    fixture_version: str
    dataset_version: str
    fixture_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    run_at: datetime
    runner_name: Literal["formal_application_static_fake"]
    external_model_called: Literal[False]
    real_device_accessed: Literal[False]
    network_accessed: Literal[False]
    rules_modified_for_results: Literal[False]
    cases: tuple[AdversarialCaseResult, ...]
    outcome_counts: dict[AdversarialOutcome, int]

    def semantic_payload(self) -> dict[str, Any]:
        """Return the repeatability surface, excluding time and commit metadata."""
        return {
            "report_kind": self.report_kind,
            "fixture_version": self.fixture_version,
            "dataset_version": self.dataset_version,
            "fixture_hash": self.fixture_hash,
            "runner_name": self.runner_name,
            "external_model_called": self.external_model_called,
            "real_device_accessed": self.real_device_accessed,
            "network_accessed": self.network_accessed,
            "rules_modified_for_results": self.rules_modified_for_results,
            # Runtime-generated diagnosis/evidence IDs are intentionally excluded;
            # repeatability is judged on behavior, not random persistence identifiers.
            "cases": [
                case.model_dump(mode="json", exclude={"cited_evidence_ids"}) for case in self.cases
            ],
            "outcome_counts": {key.value: value for key, value in self.outcome_counts.items()},
        }

    def semantic_hash(self) -> str:
        return sha256_text(canonical_json(self.semantic_payload()))


def load_adversarial_fixture(path: Path) -> AdversarialFixture:
    try:
        return AdversarialFixture.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise AdversarialProtocolError(f"无法加载 adversarial fixture: {path.name}") from exc


def classify_outcome(
    *, oracle_passed: bool, expected_degradation: bool, eval_detected_error: bool
) -> AdversarialOutcome:
    if oracle_passed:
        return (
            AdversarialOutcome.SAFE_DEGRADATION_CORRECT
            if expected_degradation
            else AdversarialOutcome.SYSTEM_CORRECT_GATE_CORRECT
        )
    return (
        AdversarialOutcome.SYSTEM_ERROR_CAUGHT_BY_EVAL
        if eval_detected_error
        else AdversarialOutcome.SYSTEM_ERROR_MISSED_BY_EVAL
    )


__all__ = [
    "AdversarialCase",
    "AdversarialCaseResult",
    "AdversarialFixture",
    "AdversarialOutcome",
    "AdversarialProtocolError",
    "AdversarialReport",
    "OracleCheck",
    "SafetyOracle",
    "TrapType",
    "classify_outcome",
    "load_adversarial_fixture",
]
