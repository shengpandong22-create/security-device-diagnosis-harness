"""alarm__query_signal：只读查询报警触发信号。"""

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


class AlarmSignalInput(BaseModel):
    """alarm__query_signal 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)


class AlarmSignalTool(BaseTool):
    """查询报警触发时信号值、阈值、噪声和信号状态。"""

    name = "alarm__query_signal"
    description = "查询报警触发时的信号值与噪声"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AlarmSignalInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AlarmSignalInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            signal = context.device_gateway.query_alarm_signal(
                arguments.device_id, arguments.alarm_id
            )
        except Exception as exc:
            return failure_result(self.name, f"查询报警信号失败: {exc}")

        observation = (
            f"报警信号 {signal.alarm_id} signal={signal.signal_value}，"
            f"threshold={signal.threshold}，noise={signal.noise_level}，"
            f"status={signal.status.value}，噪声异常={signal.is_noisy}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ALARM_SIGNAL,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=signal.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=signal.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": signal.device_id,
                "alarm_id": signal.alarm_id,
                "signal_status": signal.status.value,
                "is_noisy": signal.is_noisy,
            },
        )
