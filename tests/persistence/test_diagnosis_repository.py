"""Phase 6A：SQLite 诊断仓储验收。

覆盖：聚合往返、Evidence / Conclusion / HumanReview / 枚举 / 时间恢复、
重复 save 与不存在 update 的受控失败、读后本地修改不隐式落库、敏感字段不明文。
"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.application.errors import (
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
)
from security_diagnosis_harness.domain.conclusion import ConclusionConfidence
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.review import HumanReviewAction
from tests.persistence._builders import (
    RAW_PASSWORD,
    RAW_TOKEN,
    build_case_with_raw_credentials,
    build_confirmed_case,
)


def test_save_and_get_round_trip(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    loaded = diagnosis_repository.get(case.diagnosis_id)

    assert loaded.diagnosis_id == case.diagnosis_id
    assert loaded.device_id == case.device_id
    assert loaded.reporter == case.reporter
    assert loaded.description == case.description


def test_fault_type_and_status_enums_are_restored(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    loaded = diagnosis_repository.get(case.diagnosis_id)

    assert loaded.fault_type is SecurityFaultType.CAMERA_BLACK_SCREEN
    assert loaded.status is SecurityDiagnosisStatus.CONFIRMED


def test_evidence_is_saved_and_restored(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    loaded = diagnosis_repository.get(case.diagnosis_id)

    assert len(loaded.evidence) == len(case.evidence)
    by_id = {item.evidence_id: item for item in loaded.evidence}
    original = {item.evidence_id: item for item in case.evidence}
    assert set(by_id) == set(original)
    for evidence_id, item in by_id.items():
        assert item.evidence_type is original[evidence_id].evidence_type
        assert item.reliability is original[evidence_id].reliability
        assert item.payload == original[evidence_id].payload
        assert item.content_hash == original[evidence_id].content_hash


def test_conclusion_and_cited_evidence_ids_are_restored(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    loaded = diagnosis_repository.get(case.diagnosis_id)

    assert loaded.conclusion is not None
    assert loaded.conclusion.conclusion_id == case.conclusion.conclusion_id
    assert loaded.conclusion.confidence is ConclusionConfidence.PROBABLE
    assert loaded.conclusion.cited_evidence_ids == case.conclusion.cited_evidence_ids


def test_human_review_is_restored(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    loaded = diagnosis_repository.get(case.diagnosis_id)

    assert len(loaded.reviews) == 1
    review = loaded.reviews[0]
    assert review.action is HumanReviewAction.CONFIRM
    assert review.reviewer == "expert"
    assert review.comment == "与现场一致"


def test_timestamps_are_restored_as_aware_utc(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    loaded = diagnosis_repository.get(case.diagnosis_id)

    assert loaded.created_at.tzinfo is not None
    assert loaded.updated_at.tzinfo is not None
    assert loaded.created_at == case.created_at
    assert loaded.updated_at == case.updated_at


def test_get_unknown_raises_controlled_not_found(diagnosis_repository):
    with pytest.raises(DiagnosisNotFoundError):
        diagnosis_repository.get("missing")


def test_duplicate_save_is_rejected(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)

    with pytest.raises(DiagnosisAlreadyExistsError):
        diagnosis_repository.save(case)


def test_update_unknown_is_rejected(diagnosis_repository):
    with pytest.raises(DiagnosisNotFoundError):
        diagnosis_repository.update(build_confirmed_case("diag-unknown"))


def test_local_mutation_does_not_implicitly_persist(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)

    loaded = diagnosis_repository.get(case.diagnosis_id)
    loaded.description = "外部改坏了"
    loaded.status = SecurityDiagnosisStatus.REJECTED

    fresh = diagnosis_repository.get(case.diagnosis_id)
    assert fresh.description == case.description
    assert fresh.status is SecurityDiagnosisStatus.CONFIRMED


def test_explicit_update_persists_changes(diagnosis_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)

    loaded = diagnosis_repository.get(case.diagnosis_id)
    loaded.description = "补充说明"
    diagnosis_repository.update(loaded)

    assert diagnosis_repository.get(case.diagnosis_id).description == "补充说明"


def test_exists_and_list(diagnosis_repository):
    first = build_confirmed_case("diag-1")
    second = build_confirmed_case("diag-2")
    diagnosis_repository.save(first)
    diagnosis_repository.save(second)

    assert diagnosis_repository.exists("diag-1") is True
    assert diagnosis_repository.exists("diag-missing") is False
    assert [item.diagnosis_id for item in diagnosis_repository.list()] == ["diag-1", "diag-2"]


def test_sensitive_credentials_are_not_stored_in_plaintext(engine, diagnosis_repository):
    """明文凭证经 Domain 脱敏后入库；直接检查数据库里的原始 JSON 字节。"""
    from sqlalchemy import select

    from security_diagnosis_harness.adapters.persistence.models import DiagnosisCaseRow
    from security_diagnosis_harness.domain.device import REDACTED_VALUE

    case = build_case_with_raw_credentials()
    diagnosis_repository.save(case)

    with engine.connect() as connection:
        raw_evidence = connection.execute(select(DiagnosisCaseRow.evidence)).scalar_one()

    dumped = str(raw_evidence)
    assert RAW_PASSWORD not in dumped
    assert RAW_TOKEN not in dumped
    assert REDACTED_VALUE in dumped

    loaded = diagnosis_repository.get(case.diagnosis_id)
    assert loaded.evidence[0].payload["password"] == REDACTED_VALUE
    assert loaded.evidence[0].payload["access_token"] == REDACTED_VALUE


def test_repository_satisfies_diagnosis_port(diagnosis_repository):
    from security_diagnosis_harness.ports.diagnosis_repository import DiagnosisRepository

    assert isinstance(diagnosis_repository, DiagnosisRepository)
