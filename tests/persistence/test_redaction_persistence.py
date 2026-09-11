"""Phase 6A 审计修复：自由文本脱敏入库验收。

关键原则：测试直接把**明文**交给 Domain / Application，
绝不提前脱敏，以此验证第一脱敏入口在 Domain 而不是 Persistence Adapter。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from security_diagnosis_harness.adapters.persistence.models import DiagnosisCaseRow
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
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction

DIAGNOSIS_ID = "diag-redact-1"
RAW_PASSWORD = "plain-secret-token"
RAW_BEARER = "super-bearer-value"
RAW_API_KEY = "AKIAIOSFODNN7EXAMPLE"


def _case(description: str = "") -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=DIAGNOSIS_ID,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id="cam-1",
        reporter="tester",
        description=description,
    )


def _raw_columns(engine) -> dict:
    with engine.connect() as connection:
        row = connection.execute(select(DiagnosisCaseRow)).mappings().one()
    return dict(row)


# ---------------------------------------------------------------- 领域层脱敏
def test_description_password_is_redacted_in_domain():
    case = _case(f"password={RAW_PASSWORD}")
    assert RAW_PASSWORD not in case.description
    assert REDACTED_VALUE in case.description


def test_review_comment_bearer_token_is_redacted_in_domain():
    review = HumanReview(
        diagnosis_id=DIAGNOSIS_ID,
        action=HumanReviewAction.REQUEST_MORE_INFO,
        reviewer="expert",
        comment=f"Authorization: Bearer {RAW_BEARER}",
    )
    assert RAW_BEARER not in review.comment
    assert REDACTED_VALUE in review.comment


def test_evidence_summary_inline_token_is_redacted():
    evidence = DiagnosisEvidence(
        diagnosis_id=DIAGNOSIS_ID,
        evidence_type=EvidenceType.DEVICE_STATUS,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary=f"设备返回 access_token={RAW_PASSWORD}",
    )
    assert RAW_PASSWORD not in evidence.summary
    assert evidence.redacted is True


def test_evidence_payload_nested_secret_is_redacted():
    evidence = DiagnosisEvidence(
        diagnosis_id=DIAGNOSIS_ID,
        evidence_type=EvidenceType.DEVICE_CONFIG,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="配置快照",
        payload={"network": {"secret": "nested-secret-value"}, "ports": [1, 2]},
    )
    assert "nested-secret-value" not in str(evidence.payload)
    assert evidence.payload["network"]["secret"] == REDACTED_VALUE


def test_conclusion_root_cause_password_is_redacted():
    conclusion = DiagnosisConclusion(
        diagnosis_id=DIAGNOSIS_ID,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        summary="summary 正常",
        root_cause=f"password={RAW_PASSWORD} 导致连接失败",
        confidence=ConclusionConfidence.POSSIBLE,
        next_steps=[f"Bearer {RAW_BEARER}"],
    )
    assert RAW_PASSWORD not in (conclusion.root_cause or "")
    assert RAW_BEARER not in " ".join(conclusion.next_steps)


def test_evidence_content_hash_is_based_on_redacted_content():
    """hash 必须来自脱敏后的内容，不能与明文绑定。"""
    redacted_evidence = DiagnosisEvidence(
        diagnosis_id=DIAGNOSIS_ID,
        evidence_type=EvidenceType.DEVICE_STATUS,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary=f"token={RAW_PASSWORD}",
    )
    expected = content_hash(
        {
            "evidence_type": redacted_evidence.evidence_type.value,
            "source": redacted_evidence.source.value,
            "summary": redacted_evidence.summary,
            "payload": redacted_evidence.payload,
        }
    )
    plaintext_hash = content_hash(
        {
            "evidence_type": EvidenceType.DEVICE_STATUS.value,
            "source": EvidenceSource.DEVICE_GATEWAY.value,
            "summary": f"token={RAW_PASSWORD}",
            "payload": {},
        }
    )

    assert redacted_evidence.content_hash == expected
    assert redacted_evidence.content_hash != plaintext_hash


def test_evidence_hash_is_stable_for_same_redacted_payload():
    def build() -> DiagnosisEvidence:
        return DiagnosisEvidence(
            diagnosis_id=DIAGNOSIS_ID,
            evidence_type=EvidenceType.DEVICE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=f"password={RAW_PASSWORD}",
            payload={"api_key": RAW_API_KEY},
        )

    assert build().content_hash == build().content_hash


# ---------------------------------------------------------------- 入库验证
def test_description_password_is_not_stored_in_database(engine):
    from security_diagnosis_harness.adapters.persistence import (
        SqlAlchemyDiagnosisRepository,
    )

    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    repository.save(_case(f"password={RAW_PASSWORD}"))

    columns = _raw_columns(engine)
    assert RAW_PASSWORD not in str(columns)
    assert REDACTED_VALUE in str(columns["description"])


def test_full_aggregate_plaintext_never_reaches_database(engine):
    from security_diagnosis_harness.adapters.persistence import (
        SqlAlchemyDiagnosisRepository,
    )

    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    case = _case(f"password={RAW_PASSWORD}")
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    first = case.add_evidence(
        DiagnosisEvidence(
            diagnosis_id=DIAGNOSIS_ID,
            evidence_type=EvidenceType.DEVICE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=f"Bearer {RAW_BEARER}",
            payload={"nested": {"client_secret": "nested-secret-value"}},
        )
    )
    second = case.add_evidence(
        DiagnosisEvidence(
            diagnosis_id=DIAGNOSIS_ID,
            evidence_type=EvidenceType.DEVICE_CONFIG,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="配置",
            payload={"api_key": RAW_API_KEY},
        )
    )
    case.set_conclusion(
        DiagnosisConclusion(
            diagnosis_id=DIAGNOSIS_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            summary=f"api_key={RAW_API_KEY}",
            root_cause=f"password={RAW_PASSWORD}",
            confidence=ConclusionConfidence.PROBABLE,
            cited_evidence_ids=[first.evidence_id, second.evidence_id],
        )
    )
    case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)
    case.apply_human_review(
        HumanReview(
            diagnosis_id=DIAGNOSIS_ID,
            action=HumanReviewAction.CONFIRM,
            reviewer="expert",
            comment=f"Bearer {RAW_BEARER}",
        )
    )
    repository.save(case)

    columns = _raw_columns(engine)
    serialized = " ".join(str(value) for value in columns.values())

    for raw in (RAW_PASSWORD, RAW_BEARER, RAW_API_KEY, "nested-secret-value"):
        assert raw not in serialized, f"明文 {raw} 泄漏进数据库"
    assert REDACTED_VALUE in serialized


def test_round_trip_preserves_redacted_values(engine):
    from security_diagnosis_harness.adapters.persistence import (
        SqlAlchemyDiagnosisRepository,
    )

    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    repository.save(_case(f"password={RAW_PASSWORD}"))

    loaded = repository.get(DIAGNOSIS_ID)
    assert RAW_PASSWORD not in loaded.description
    assert REDACTED_VALUE in loaded.description


# ---------------------------------------------------------------- 知识域
def test_knowledge_review_comment_secret_is_redacted(engine):
    from security_diagnosis_harness.adapters.persistence import (
        SqlAlchemyKnowledgeRepository,
    )
    from security_diagnosis_harness.domain.knowledge import (
        KnowledgeCandidate,
        KnowledgeReview,
        KnowledgeReviewAction,
    )

    repository = SqlAlchemyKnowledgeRepository.from_engine(engine)
    candidate = KnowledgeCandidate(
        knowledge_id="knw-redact-1",
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
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.CONFIRM,
            reviewer="expert",
            comment="client_secret=knowledge-secret-value",
        )
    )
    repository.save(candidate)

    loaded = repository.get("knw-redact-1")
    stored_comment = loaded.reviews[0].comment
    assert "knowledge-secret-value" not in stored_comment
    assert REDACTED_VALUE in stored_comment


def test_adapter_does_not_reintroduce_plaintext(engine):
    """Persistence Adapter 不做第二套脱敏，只做安全断言。"""
    from security_diagnosis_harness.adapters.persistence import (
        SqlAlchemyDiagnosisRepository,
    )

    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    # 绕过 Domain 直接构造明文行，Adapter 不应负责修复它（说明只查不补）。
    case = _case("")
    case.description = f"password={RAW_PASSWORD}"
    repository.save(case)

    columns = _raw_columns(engine)
    # Domain 已在构造时替换 description；这里断言 Adapter 也没有把明文写入。
    assert RAW_PASSWORD not in str(columns["description"])


@pytest.mark.parametrize(
    "raw_text",
    [
        f"password={RAW_PASSWORD}",
        f"Bearer {RAW_BEARER}",
        f"api_key: {RAW_API_KEY}",
        "https://admin:secret-pwd@camera.local/live.m3u8",
        "http://example.local/snapshot.jpg?token=query-secret-value",
    ],
)
def test_redact_text_covers_common_credential_shapes(raw_text: str):
    from security_diagnosis_harness.domain.redaction import redact_text

    cleaned, changed = redact_text(raw_text)

    assert changed is True
    assert REDACTED_VALUE in cleaned
    for leaked in (
        RAW_PASSWORD,
        RAW_BEARER,
        RAW_API_KEY,
        "secret-pwd",
        "query-secret-value",
    ):
        assert leaked not in cleaned


def test_redact_text_is_idempotent():
    from security_diagnosis_harness.domain.redaction import redact_text

    once, _ = redact_text(f"password={RAW_PASSWORD}")
    twice, changed = redact_text(once)

    assert once == twice
    assert changed is False
