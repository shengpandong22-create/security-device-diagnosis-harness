"""device__read_config_snapshot：只读读取设备配置快照。"""

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


class DeviceConfigInput(BaseModel):
    """device__read_config_snapshot 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)


class DeviceConfigSnapshotTool(BaseTool):
    """读取设备配置快照，凭证字段必须已被脱敏。"""

    name = "device__read_config_snapshot"
    description = "读取设备编码、分辨率、码率等配置快照"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = DeviceConfigInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, DeviceConfigInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            config = context.device_gateway.read_config_snapshot(arguments.device_id)
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"读取设备配置失败: {exc}")

        observation = (
            f"设备 {config.device_id} 启用={config.enabled}，"
            f"编码={config.encoding or '-'}，"
            f"分辨率={config.resolution or '-'}，"
            f"帧率={config.frame_rate or '-'}，"
            f"码率={config.bitrate_kbps or '-'}kbps，"
            f"已脱敏={config.redacted}"
        )
        payload = config.model_dump(mode="json")
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.DEVICE_CONFIG,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=payload,
            reliability=Reliability.MEDIUM,
            redacted=config.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={"device_id": config.device_id, "redacted": config.redacted},
        )
