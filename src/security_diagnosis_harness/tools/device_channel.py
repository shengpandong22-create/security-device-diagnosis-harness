"""device__query_channel：只读查询设备通道快照。"""

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


class DeviceChannelInput(BaseModel):
    """device__query_channel 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    channel_id: str | None = None


class DeviceChannelTool(BaseTool):
    """查询通道在线状态、绑定状态与平台注册状态。"""

    name = "device__query_channel"
    description = "查询设备通道在线、绑定与平台注册状态"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = DeviceChannelInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, DeviceChannelInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            channel = context.device_gateway.query_channel_snapshot(arguments.device_id)
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询设备通道失败: {exc}")

        observation = (
            f"设备 {channel.device_id} 通道 {channel.channel_id} "
            f"状态={channel.channel_status.value}，"
            f"已绑定={channel.bound}，"
            f"平台已注册={channel.platform_registered}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.DEVICE_CHANNEL,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=channel.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=channel.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={"device_id": channel.device_id, "channel_id": channel.channel_id},
        )
