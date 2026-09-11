"""confirmed knowledge 接入 knowledge__search 的契约验收。"""

from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeReview,
    KnowledgeReviewAction,
)
from security_diagnosis_harness.tools.knowledge_search import KnowledgeSearchTool
from security_diagnosis_harness.tools.registry import ToolRegistry

from ..conftest import make_tool_context


def _candidate(confirmed: bool) -> KnowledgeCandidate:
    candidate = KnowledgeCandidate(
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        candidate_label="device_offline",
        title="现场经验：摄像头网络中断",
        summary="设备失联会导致黑屏",
        symptoms=["设备无法连接"],
        root_cause="网络链路中断",
        troubleshooting_steps=["检查交换机端口"],
        source_diagnosis_id="diag-source",
        source_conclusion_id="con-source",
        source_evidence_ids=["evd-source"],
    )
    if confirmed:
        candidate.apply_review(
            KnowledgeReview(
                knowledge_id=candidate.knowledge_id,
                action=KnowledgeReviewAction.CONFIRM,
                reviewer="expert",
            )
        )
    return candidate


def test_tool_recalls_confirmed_candidate_without_changing_payload_contract():
    repository = InMemoryKnowledgeRepository()
    confirmed = _candidate(True)
    repository.save(confirmed)
    repository.save(_candidate(False))
    registry = ToolRegistry()
    registry.register(KnowledgeSearchTool(sops=[], retriever=repository))

    result = registry.execute(
        "knowledge__search",
        {"query": "网络 中断", "limit": 3},
        make_tool_context("diag-current"),
    )

    assert result.ok is True
    assert result.metadata["matched"] == 1
    draft = result.evidence_drafts[0]
    assert draft.payload["sops"][0]["sop_id"] == confirmed.knowledge_id
    assert draft.payload["sops"][0]["source_diagnosis_id"] == "diag-source"
    assert "payload" not in draft.payload["sops"][0]
