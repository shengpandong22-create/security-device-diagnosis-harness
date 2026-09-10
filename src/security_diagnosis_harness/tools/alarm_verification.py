"""alarm__query_verification：只读查询报警复核结果。"""

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


class AlarmVerificationInput(BaseModel):
    """alarm__query_verification 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)


class AlarmVerificationTool(BaseTool):
    """查询视频 / 人工复核是否发现真实目标。"""

    name = "alarm__query_verification"
    description = "查询报警复核结果"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AlarmVerificationInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AlarmVerificationInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            verification = context.device_gateway.query_alarm_verification(
                arguments.device_id, arguments.alarm_id
            )
        except Exception as exc:
            return failure_result(self.name, f"查询报警复核失败: {exc}")

        observation = (
            f"报警复核 {verification.alarm_id} result={verification.result.value}，"
            f"target_count={verification.target_count}，"
            f"疑似误报={verification.indicates_false_alarm}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ALARM_VERIFICATION,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=verification.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=verification.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": verification.device_id,
                "alarm_id": verification.alarm_id,
                "verification_result": verification.result.value,
                "false_alarm": verification.indicates_false_alarm,
            },
        )
