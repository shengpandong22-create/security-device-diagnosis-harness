"""device__query_status：只读查询设备状态。"""

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


class DeviceStatusInput(BaseModel):
    """device__query_status 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)


class DeviceStatusTool(BaseTool):
    """查询设备在线、通道、码流、录像状态。"""

    name = "device__query_status"
    description = "查询设备在线状态、通道状态、码流状态和录像状态"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = DeviceStatusInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, DeviceStatusInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            snapshot = context.device_gateway.query_status(arguments.device_id)
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询设备状态失败: {exc}")

        observation = (
            f"设备 {snapshot.device_id} 在线={snapshot.online}，"
            f"通道在线={snapshot.channel_online}，"
            f"码流状态={snapshot.stream_status.value}，"
            f"录像状态={snapshot.recording_status.value}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.DEVICE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=snapshot.model_dump(mode="json"),
            reliability=Reliability.HIGH,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={"device_id": snapshot.device_id, "snapshot_id": snapshot.snapshot_id},
        )
