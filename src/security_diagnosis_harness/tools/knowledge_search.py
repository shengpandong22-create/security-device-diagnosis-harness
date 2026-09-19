"""knowledge__search：受治理知识与显式静态 fixture 检索。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType, Reliability
from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRetriever
from security_diagnosis_harness.tools.contracts import (
    BaseTool,
    ToolEvidenceDraft,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolPermission,
    ToolRiskLevel,
)


class KnowledgeSearchInput(BaseModel):
    """knowledge__search 参数。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    limit: int = Field(default=3, ge=1, le=10)


class KnowledgeSearchTool(BaseTool):
    """检索与当前故障类型相关的 SOP / 故障模式。"""

    name = "knowledge__search"
    description = "检索安防知识库中的 SOP 与故障模式"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.KNOWLEDGE_READ})
    input_model = KnowledgeSearchInput

    def __init__(
        self,
        sops: list[dict[str, object]] | None = None,
        retriever: KnowledgeRetriever | None = None,
    ) -> None:
        # 安全默认值必须是空集。静态 SOP 只能由 demo/test 装配显式传入，正式
        # Runtime 不得在缺少受治理 Retriever 时静默回退到代码内知识。
        self._sops = list(sops) if sops is not None else []
        self._retriever = retriever

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, KnowledgeSearchInput)
        keyword = arguments.query.strip().lower()

        matched: list[dict[str, object]] = []
        if self._retriever is not None:
            candidates = self._retriever.search_confirmed(
                arguments.query, context.fault_type, arguments.limit
            )
            matched.extend(
                {
                    "sop_id": candidate.knowledge_id,
                    "title": candidate.title,
                    "summary": candidate.summary,
                    "checks": list(candidate.troubleshooting_steps),
                    "candidate_label": candidate.candidate_label,
                    "source_diagnosis_id": candidate.source_diagnosis_id,
                    "source_evidence_ids": list(candidate.source_evidence_ids),
                }
                for candidate in candidates
            )
        for sop in self._sops:
            if len(matched) >= arguments.limit:
                break
            fault_type = sop.get("fault_type")
            if fault_type is not None and fault_type is not context.fault_type:
                continue
            haystack = f"{sop.get('title', '')} {sop.get('summary', '')}".lower()
            if keyword and keyword not in haystack:
                continue
            matched.append(sop)
            if len(matched) >= arguments.limit:
                break

        if not matched:
            observation = (
                f"未命中 SOP（query={arguments.query}，"
                f"fault_type={context.fault_type.value}）"
            )
        else:
            titles = "、".join(str(sop.get("title")) for sop in matched)
            observation = f"命中 {len(matched)} 条 SOP: {titles}"

        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.KNOWLEDGE_SOP,
            source=EvidenceSource.KNOWLEDGE_BASE,
            summary=observation,
            payload={
                "query": arguments.query,
                "fault_type": context.fault_type.value,
                "sops": [
                    {key: value for key, value in sop.items() if key != "fault_type"}
                    for sop in matched
                ],
            },
            # SOP 只是经验依据，可信度不能高于设备事实。
            reliability=Reliability.LOW,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={"query": arguments.query, "matched": len(matched)},
        )
