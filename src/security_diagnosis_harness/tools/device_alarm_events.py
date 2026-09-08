"""device__search_alarm_events：只读查询设备告警事件。"""

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


class DeviceAlarmEventsInput(BaseModel):
    """device__search_alarm_events 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    keyword: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class DeviceAlarmEventsTool(BaseTool):
    """查询设备告警、上下线、异常事件。

    告警存在只构成根因候选，不等于根因成立。
    """

    name = "device__search_alarm_events"
    description = "按关键字查询设备告警与异常事件"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = DeviceAlarmEventsInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, DeviceAlarmEventsInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            events = context.device_gateway.search_alarm_events(
                arguments.device_id,
                keyword=arguments.keyword,
                limit=arguments.limit,
            )
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询设备告警失败: {exc}")

        if not events:
            observation = (
                f"设备 {arguments.device_id} 未命中告警事件"
                f"（keyword={arguments.keyword or '-'}）"
            )
        else:
            listed = "、".join(
                f"{event.event_type}({event.severity.value})" for event in events
            )
            observation = f"设备 {arguments.device_id} 命中 {len(events)} 条告警: {listed}"

        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.DEVICE_ALARM,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload={
                "device_id": arguments.device_id,
                "keyword": arguments.keyword,
                "events": [event.model_dump(mode="json") for event in events],
            },
            reliability=Reliability.MEDIUM,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={"device_id": arguments.device_id, "matched": len(events)},
        )
