"""Phase 5：诊断确认 -> 知识审核 -> Hybrid 召回的完整本地闭环。"""

from __future__ import annotations

import json
from typing import Any

from security_diagnosis_harness.adapters.embedding.http_bge import (
    HttpBgeEmbeddingAdapter,
)
from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
)
from security_diagnosis_harness.application.hybrid_knowledge_retriever import (
    HybridKnowledgeRetriever,
)
from security_diagnosis_harness.application.knowledge_candidates import (
    KnowledgeCandidateApplicationService,
)
from security_diagnosis_harness.bootstrap.container import build_phase1_container
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeReview,
    KnowledgeReviewAction,
)
from security_diagnosis_harness.domain.review import HumanReviewAction
from security_diagnosis_harness.ports.embedding import EmbeddingPort
from security_diagnosis_harness.tools.contracts import ToolExecutionContext
from security_diagnosis_harness.tools.knowledge_search import KnowledgeSearchTool
from security_diagnosis_harness.tools.registry import ToolRegistry, default_permissions


def run_demo(embedding: EmbeddingPort | None = None) -> dict[str, Any]:
    container = build_phase1_container()
    diagnosis = container.service.create_diagnosis(
        device_id="cam-offline-01",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="phase5-demo",
        description="摄像头黑屏且设备无法连接",
    )
    container.service.run_diagnosis(diagnosis.diagnosis_id)
    container.service.review_diagnosis(
        diagnosis.diagnosis_id,
        HumanReviewAction.CONFIRM,
        reviewer="diagnosis-expert",
        comment="现场确认设备离线",
    )

    candidate = KnowledgeCandidateApplicationService(
        container.service
    ).generate_from_diagnosis(diagnosis.diagnosis_id)
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.CONFIRM,
            reviewer="knowledge-expert",
            comment="确认该经验可以复用",
        )
    )
    repository = InMemoryKnowledgeRepository()
    repository.save(candidate)

    selected_embedding = embedding or HttpBgeEmbeddingAdapter(timeout_seconds=30.0)
    retriever = HybridKnowledgeRetriever(repository, selected_embedding)
    registry = ToolRegistry()
    registry.register(KnowledgeSearchTool(sops=[], retriever=retriever))
    result = registry.execute(
        "knowledge__search",
        {"query": "设备离线 网络不可达", "limit": 3},
        ToolExecutionContext(
            diagnosis_id="diag-next-similar-case",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            permissions=default_permissions(),
        ),
    )
    recalled = result.evidence_drafts[0].payload["sops"] if result.ok else []
    return {
        "source_diagnosis_status": "confirmed",
        "knowledge_status": candidate.status.value,
        "knowledge_id": candidate.knowledge_id,
        "retrieval_mode": retriever.last_diagnostics.mode,
        "semantic_fallback": retriever.last_diagnostics.semantic_fallback,
        "recalled_count": len(recalled),
        "recalled_knowledge_ids": [item["sop_id"] for item in recalled],
        "evidence_type": (
            result.evidence_drafts[0].evidence_type.value if result.evidence_drafts else None
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
