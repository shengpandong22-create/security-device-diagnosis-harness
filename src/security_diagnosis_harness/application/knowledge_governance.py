"""知识候选持久化、人工审核与审计的应用边界。"""

from security_diagnosis_harness.application.knowledge_candidates import (
    KnowledgeCandidateApplicationService,
)
from security_diagnosis_harness.domain.audit import AuditEntityType, AuditEvent
from security_diagnosis_harness.domain.errors import KnowledgeReviewNotAllowed
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateSource,
    KnowledgeCandidateStatus,
    KnowledgeReview,
    KnowledgeReviewAction,
    ManualKnowledgeSeed,
)
from security_diagnosis_harness.ports.audit_repository import AuditRepository
from security_diagnosis_harness.ports.audited_write import AuditedWrite
from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRepository


class KnowledgeGovernanceApplicationService:
    """保证知识写入和状态变化都有追加式审计记录。"""

    def __init__(
        self,
        generator: KnowledgeCandidateApplicationService,
        repository: KnowledgeRepository,
        audit_repository: AuditRepository,
        audited_write: AuditedWrite | None = None,
    ) -> None:
        self._generator = generator
        self._repository = repository
        self._audit_repository = audit_repository
        if audited_write is None:
            raise ValueError("KnowledgeGovernance 启用审计时必须提供 AuditedWrite")
        self._audited_write = audited_write

    def generate_and_save(self, diagnosis_id: str, actor: str) -> KnowledgeCandidate:
        candidate = self._generator.generate_from_diagnosis(diagnosis_id)
        event = AuditEvent(
            entity_type=AuditEntityType.KNOWLEDGE,
            entity_id=candidate.knowledge_id,
            action="knowledge.generated",
            actor=actor,
            current_state=candidate.status.value,
            current_version=1,
            summary="从人工确认诊断生成知识候选",
            metadata={"source_diagnosis_id": candidate.source_diagnosis_id},
        )
        return self._audited_write.save_knowledge(candidate, event)

    def import_manual_seed(
        self, seed: ManualKnowledgeSeed, actor: str
    ) -> KnowledgeCandidate:
        """把摘要校验通过的手工资料导入 candidate 池，不自动确认。"""
        candidate = KnowledgeCandidate(
            fault_type=seed.fault_type,
            candidate_label=seed.candidate_label,
            title=seed.title,
            summary=seed.summary,
            symptoms=list(seed.symptoms),
            root_cause=seed.root_cause,
            troubleshooting_steps=list(seed.troubleshooting_steps),
            excluded_causes=list(seed.excluded_causes),
            source=KnowledgeCandidateSource.MANUAL_SEED,
            source_artifact_id=seed.artifact_id,
            source_artifact_sha256=seed.artifact_sha256,
            status=KnowledgeCandidateStatus.CANDIDATE,
            metadata={"source_import_actor": actor},
        )
        event = AuditEvent(
            entity_type=AuditEntityType.KNOWLEDGE,
            entity_id=candidate.knowledge_id,
            action="knowledge.manual_seed.imported",
            actor=actor,
            current_state=candidate.status.value,
            current_version=1,
            summary="导入手工知识种子为待审核候选",
            metadata={
                "source_artifact_id": seed.artifact_id,
                "source_artifact_sha256": seed.artifact_sha256,
            },
        )
        return self._audited_write.save_knowledge(candidate, event)

    def review(
        self,
        knowledge_id: str,
        action: KnowledgeReviewAction,
        reviewer: str,
        comment: str = "",
    ) -> KnowledgeCandidate:
        candidate = self._repository.get(knowledge_id)
        importer = candidate.metadata.get("source_import_actor")
        if (
            candidate.source
            in {KnowledgeCandidateSource.MANUAL_SEED, KnowledgeCandidateSource.IMPORTED}
            and importer == reviewer
        ):
            raise KnowledgeReviewNotAllowed("知识导入者不能审核自己导入的候选")
        previous_state = candidate.status.value
        previous_version = candidate.version
        candidate.apply_review(
            KnowledgeReview(
                knowledge_id=knowledge_id,
                action=action,
                reviewer=reviewer,
                comment=comment,
            )
        )
        event = AuditEvent(
            entity_type=AuditEntityType.KNOWLEDGE,
            entity_id=candidate.knowledge_id,
            action=f"knowledge.review.{action.value}",
            actor=reviewer,
            previous_state=previous_state,
            current_state=candidate.status.value,
            previous_version=previous_version,
            current_version=previous_version + 1,
            summary="人工审核知识候选",
        )
        return self._audited_write.update_knowledge(candidate, event)

    def get(self, knowledge_id: str) -> KnowledgeCandidate:
        return self._repository.get(knowledge_id)
