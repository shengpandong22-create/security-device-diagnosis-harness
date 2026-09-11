"""knowledge__search：最小静态知识/SOP 检索。

Phase 0B 只做内存静态匹配，不做向量数据库，也不做 RAG。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.enums import SecurityFaultType
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

DEFAULT_SOPS: list[dict[str, object]] = [
    {
        "sop_id": "sop-camera-black-screen-001",
        "fault_type": SecurityFaultType.CAMERA_BLACK_SCREEN,
        "title": "摄像头黑屏排查 SOP",
        "summary": "依次确认设备在线、通道在线、码流状态、编码器与平台拉流状态。",
        "checks": [
            "确认设备与通道在线",
            "确认主码流发布状态",
            "检查编码器是否超时",
            "检查码率与分辨率是否超出设备能力",
            "确认平台侧拉流是否正常",
        ],
    },
    {
        "sop_id": "sop-recording-missing-001",
        "fault_type": SecurityFaultType.RECORDING_MISSING,
        "title": "录像缺失排查 SOP",
        "summary": "确认录像计划、存储状态、磁盘容量与 NVR 通道绑定关系。",
        "checks": [
            "确认录像计划时间段",
            "确认存储盘状态与容量",
            "确认 NVR 通道绑定",
        ],
    },
    {
        "sop_id": "sop-access-card-failed-001",
        "fault_type": SecurityFaultType.ACCESS_CARD_FAILED,
        "title": "门禁刷卡异常排查 SOP",
        "summary": "确认卡片权限、控制器在线状态与读卡器接线。",
        "checks": [
            "确认卡片权限与有效期",
            "确认控制器在线",
            "确认读卡器状态",
        ],
    },
    {
        "sop_id": "sop-alarm-false-positive-001",
        "fault_type": SecurityFaultType.ALARM_FALSE_POSITIVE,
        "title": "报警误报排查 SOP",
        "summary": "确认探测器灵敏度、防区类型与布撤防时间。",
        "checks": [
            "确认探测器灵敏度",
            "确认防区类型配置",
            "确认布撤防时间段",
        ],
    },
]


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
        self._sops = sops if sops is not None else list(DEFAULT_SOPS)
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
