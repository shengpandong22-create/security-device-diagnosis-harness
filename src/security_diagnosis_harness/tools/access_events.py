"""access__search_events：只读查询近期门禁通行事件。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType, Reliability
from security_diagnosis_harness.tools.contracts import (
    BaseTool,
    ToolEvidenceDraft,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolPermission,
    ToolRiskLevel,
    failure_result,
)


class AccessEventsInput(BaseModel):
    """access__search_events 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    door_id: str = Field(min_length=1)
    credential_id: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class AccessEventsTool(BaseTool):
    """查询指定门和凭证的近期通行事件。"""

    name = "access__search_events"
    description = "查询近期门禁通行事件和拒绝原因"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AccessEventsInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AccessEventsInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            events = context.device_gateway.search_access_events(
                arguments.device_id,
                arguments.door_id,
                arguments.credential_id,
                arguments.limit,
            )
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询门禁事件失败: {exc}")

        if not events:
            observation = (
                f"设备 {arguments.device_id} 门 {arguments.door_id} "
                f"凭证查询未命中近期门禁事件"
            )
            return ToolExecutionResult(
                tool_name=self.name,
                observation=observation,
                evidence_drafts=[],
                metadata={"matched": 0},
            )

        first = events[0]
        observation = (
            f"命中 {len(events)} 条门禁事件，最近一次 "
            f"decision={first.decision.value}，"
            f"deny_reason={first.deny_reason.value if first.deny_reason else '-'}，"
            f"occurred_at={first.occurred_at.isoformat()}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ACCESS_EVENT,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload={"events": [event.model_dump(mode="json") for event in events]},
            reliability=Reliability.HIGH,
            redacted=any(event.redacted for event in events),
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": arguments.device_id,
                "door_id": arguments.door_id,
                "matched": len(events),
                "latest_decision": first.decision.value,
                "latest_deny_reason": first.deny_reason.value if first.deny_reason else None,
            },
        )
