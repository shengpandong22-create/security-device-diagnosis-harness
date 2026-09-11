"""Phase 6A 收尾：构造后修改（post-construction mutation）的深层脱敏验收。

Pydantic 允许在构造之后就地修改字段，这类修改不会再触发 `model_validator`。
本文件验证持久化边界会把**整个聚合**重新走一遍 Domain 校验，
从而堵住"构造后再注入明文"的绕过路径。
"""

from __future__ import annotations

from sqlalchemy import select

from security_diagnosis_harness.adapters.persistence import (
    SqlAlchemyDiagnosisRepository,
    SqlAlchemyKnowledgeRepository,
)
from security_diagnosis_harness.adapters.persistence.models import (
    DiagnosisCaseRow,
    KnowledgeCandidateRow,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.common import content_hash
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
)
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeReview,
    KnowledgeReviewAction,
)
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction

DIAGNOSIS_ID = "diag-mutate-1"
SECRET = "MUTATED-PLAIN-SECRET"


def _case() -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=DIAGNOSIS_ID,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id="cam-1",
        reporter="tester",
    )


def _evidence(summary: str = "safe", payload: dict | None = None) -> DiagnosisEvidence:
    return DiagnosisEvidence(
        diagnosis_id=DIAGNOSIS_ID,
        evidence_type=EvidenceType.DEVICE_CONFIG,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary=summary,
        payload=payload if payload is not None else {"safe": "ok"},
    )


def _raw_case_row(engine) -> dict:
    with engine.connect() as connection:
        return dict(connection.execute(select(DiagnosisCaseRow)).mappings().one())


def _raw_knowledge_row(engine) -> dict:
    with engine.connect() as connection:
        return dict(connection.execute(select(KnowledgeCandidateRow)).mappings().one())


def _knowledge() -> KnowledgeCandidate:
    return KnowledgeCandidate(
        knowledge_id="knw-mutate-1",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        candidate_label="device_offline",
        title="摄像头离线黑屏",
        summary="设备不可达",
        symptoms=["画面黑屏"],
        root_cause="网络中断",
        troubleshooting_steps=["检查网络"],
        source_diagnosis_id=DIAGNOSIS_ID,
        source_conclusion_id="con-1",
        source_evidence_ids=["evd-1"],
    )


# ---------------------------------------------------------------- 场景 1-7
def test_evidence_payload_password_injected_after_construction(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    evidence = _evidence()
    evidence.payload["password"] = SECRET
    case.evidence.append(evidence)

    repository.save(case)

    row = _raw_case_row(engine)
    assert SECRET not in str(row["evidence"])
    assert REDACTED_VALUE in str(row["evidence"])


def test_evidence_summary_token_injected_after_construction(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    evidence = _evidence()
    evidence.summary = f"token={SECRET}"
    case.evidence.append(evidence)

    repository.save(case)

    assert SECRET not in str(_raw_case_row(engine)["evidence"])


def test_conclusion_root_cause_password_injected_after_construction(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    first = _evidence()
    second = _evidence(summary="配置", payload={"encoding": "H264"})
    case.evidence.extend([first, second])
    case.set_conclusion(
        DiagnosisConclusion(
            diagnosis_id=DIAGNOSIS_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            summary="正常摘要",
            root_cause="正常根因",
            confidence=ConclusionConfidence.PROBABLE,
            cited_evidence_ids=[first.evidence_id, second.evidence_id],
        )
    )
    # 构造后注入明文
    case.conclusion.root_cause = f"password={SECRET}"

    repository.save(case)

    row = _raw_case_row(engine)
    assert SECRET not in str(row["conclusion"])
    assert REDACTED_VALUE in str(row["conclusion"])


def test_human_review_comment_bearer_injected_after_construction(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    first = _evidence()
    second = _evidence(summary="配置", payload={"encoding": "H264"})
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    case.add_evidence(first)
    case.add_evidence(second)
    case.set_conclusion(
        DiagnosisConclusion(
            diagnosis_id=DIAGNOSIS_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            summary="正常摘要",
            confidence=ConclusionConfidence.PROBABLE,
            cited_evidence_ids=[first.evidence_id, second.evidence_id],
        )
    )
    case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)
    review = HumanReview(
        diagnosis_id=DIAGNOSIS_ID,
        action=HumanReviewAction.CONFIRM,
        reviewer="expert",
        comment="ok",
    )
    case.apply_human_review(review)
    # 构造后注入 Bearer
    case.reviews[0].comment = f"Bearer {SECRET}"

    repository.save(case)

    row = _raw_case_row(engine)
    assert SECRET not in str(row["reviews"])
    assert REDACTED_VALUE in str(row["reviews"])


def test_knowledge_metadata_client_secret_injected_after_construction(engine):
    repository = SqlAlchemyKnowledgeRepository.from_engine(engine)
    candidate = _knowledge()
    candidate.metadata["nested"] = {"client_secret": SECRET}

    repository.save(candidate)

    row = _raw_knowledge_row(engine)
    assert SECRET not in str(row["metadata"])
    assert REDACTED_VALUE in str(row["metadata"])


def test_knowledge_symptoms_appended_after_construction(engine):
    repository = SqlAlchemyKnowledgeRepository.from_engine(engine)
    candidate = _knowledge()
    candidate.symptoms.append(f"password={SECRET}")

    repository.save(candidate)

    row = _raw_knowledge_row(engine)
    assert SECRET not in str(row["symptoms"])
    assert REDACTED_VALUE in str(row["symptoms"])


def test_knowledge_review_comment_secret_injected_after_construction(engine):
    repository = SqlAlchemyKnowledgeRepository.from_engine(engine)
    candidate = _knowledge()
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.CONFIRM,
            reviewer="expert",
            comment="ok",
        )
    )
    candidate.reviews[0].comment = f"secret={SECRET}"

    repository.save(candidate)

    row = _raw_knowledge_row(engine)
    assert SECRET not in str(row["reviews"])
    assert REDACTED_VALUE in str(row["reviews"])


# ---------------------------------------------------------------- 场景 8-10
def test_all_plaintext_absent_from_raw_sqlite_columns(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    case.description = f"password={SECRET}"
    evidence = _evidence()
    evidence.payload["token"] = SECRET
    evidence.summary = f"Bearer {SECRET}"
    case.evidence.append(evidence)

    repository.save(case)

    serialized = " ".join(str(value) for value in _raw_case_row(engine).values())
    assert SECRET not in serialized
    assert REDACTED_VALUE in serialized


def test_caller_object_is_not_mutated_by_repository(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    evidence = _evidence()
    evidence.payload["password"] = SECRET
    case.evidence.append(evidence)

    repository.save(case)

    # 调用方原对象仍保持其口径（Repository 只序列化安全副本）
    assert case.evidence[0].payload["password"] == SECRET
    assert case.evidence[0].content_hash == evidence.content_hash


def test_repository_returns_sanitized_object(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    evidence = _evidence()
    evidence.payload["password"] = SECRET
    case.evidence.append(evidence)

    returned = repository.save(case)

    assert returned.evidence[0].payload["password"] == REDACTED_VALUE
    assert returned.evidence[0].redacted is True


# ---------------------------------------------------------------- Evidence hash
def test_hash_recomputed_after_payload_mutation(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    evidence = _evidence()
    old_hash = evidence.content_hash
    evidence.payload["password"] = SECRET
    case.evidence.append(evidence)

    repository.save(case)
    loaded = repository.get(DIAGNOSIS_ID)

    assert loaded.evidence[0].content_hash != old_hash
    assert loaded.evidence[0].content_hash == content_hash(
        {
            "evidence_type": EvidenceType.DEVICE_CONFIG.value,
            "source": EvidenceSource.DEVICE_GATEWAY.value,
            "summary": loaded.evidence[0].summary,
            "payload": loaded.evidence[0].payload,
        }
    )


def test_persisted_hash_matches_persisted_content(engine):
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case()
    evidence = _evidence()
    evidence.payload["secret"] = SECRET
    case.evidence.append(evidence)

    repository.save(case)

    loaded = repository.get(DIAGNOSIS_ID)
    item = loaded.evidence[0]
    assert item.content_hash == content_hash(
        {
            "evidence_type": item.evidence_type.value,
            "source": item.source.value,
            "summary": item.summary,
            "payload": item.payload,
        }
    )
    # 数据库里的 hash 就是读回对象的 hash
    assert item.content_hash in str(_raw_case_row(engine)["evidence"])


def test_construction_time_hash_is_stable_for_unmutated_object():
    first = _evidence()
    second = _evidence()
    assert first.content_hash == second.content_hash
    assert first.content_hash != ""
