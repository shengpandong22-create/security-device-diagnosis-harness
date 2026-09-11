"""知识候选持久化、人工审核与审计的应用边界。"""

from security_diagnosis_harness.application.knowledge_candidates import (
    KnowledgeCandidateApplicationService,
)
from security_diagnosis_harness.domain.audit import AuditEntityType, AuditEvent
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeReview,
    KnowledgeReviewAction,
)
from security_diagnosis_harness.ports.audit_repository import AuditRepository
from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRepository


class KnowledgeGovernanceApplicationService:
    """保证知识写入和状态变化都有追加式审计记录。"""

    def __init__(
        self,
        generator: KnowledgeCandidateApplicationService,
        repository: KnowledgeRepository,
        audit_repository: AuditRepository,
    ) -> None:
        self._generator = generator
        self._repository = repository
        self._audit_repository = audit_repository

    def generate_and_save(self, diagnosis_id: str, actor: str) -> KnowledgeCandidate:
        candidate = self._generator.generate_from_diagnosis(diagnosis_id)
        saved = self._repository.save(candidate)
        self._audit_repository.append(
            AuditEvent(
                entity_type=AuditEntityType.KNOWLEDGE,
                entity_id=saved.knowledge_id,
                action="knowledge.generated",
                actor=actor,
                current_state=saved.status.value,
                current_version=saved.version,
                summary="从人工确认诊断生成知识候选",
                metadata={"source_diagnosis_id": saved.source_diagnosis_id},
            )
        )
        return saved

    def review(
        self,
        knowledge_id: str,
        action: KnowledgeReviewAction,
        reviewer: str,
        comment: str = "",
    ) -> KnowledgeCandidate:
        candidate = self._repository.get(knowledge_id)
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
        saved = self._repository.update(candidate)
        self._audit_repository.append(
            AuditEvent(
                entity_type=AuditEntityType.KNOWLEDGE,
                entity_id=saved.knowledge_id,
                action=f"knowledge.review.{action.value}",
                actor=reviewer,
                previous_state=previous_state,
                current_state=saved.status.value,
                previous_version=previous_version,
                current_version=saved.version,
                summary="人工审核知识候选",
            )
        )
        return saved

    def get(self, knowledge_id: str) -> KnowledgeCandidate:
        return self._repository.get(knowledge_id)
