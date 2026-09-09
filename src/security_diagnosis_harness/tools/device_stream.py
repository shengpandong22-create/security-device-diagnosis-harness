"""device__query_stream：只读查询设备码流快照。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.camera import StreamKind
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


class DeviceStreamInput(BaseModel):
    """device__query_stream 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    stream_kind: StreamKind = StreamKind.MAIN


class DeviceStreamTool(BaseTool):
    """查询主/子码流取流状态、编码、分辨率、帧率、码率和错误码。"""

    name = "device__query_stream"
    description = "查询设备主/子码流的取流状态与编码参数"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = DeviceStreamInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, DeviceStreamInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            stream = context.device_gateway.query_stream_snapshot(
                arguments.device_id,
                arguments.stream_kind,
            )
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询设备码流失败: {exc}")

        observation = (
            f"设备 {stream.device_id} {stream.stream_kind.value} 码流 "
            f"取流={stream.pull_status.value}，"
            f"编码={stream.encoding or '-'}，"
            f"分辨率={stream.resolution or '-'}，"
            f"帧率={stream.frame_rate or '-'}，"
            f"码率={stream.bitrate_kbps or '-'}kbps，"
            f"错误码={stream.error_code or '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.DEVICE_STREAM,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=stream.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=stream.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": stream.device_id,
                "stream_kind": stream.stream_kind.value,
                "pull_status": stream.pull_status.value,
            },
        )
