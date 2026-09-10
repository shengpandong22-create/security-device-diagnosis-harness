"""alarm__query_correlation：只读查询重复 / 关联告警。"""

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


class AlarmCorrelationInput(BaseModel):
    """alarm__query_correlation 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)


class AlarmCorrelationTool(BaseTool):
    """查询短时间重复告警、相邻设备关联告警和告警模式。"""

    name = "alarm__query_correlation"
    description = "查询重复和关联告警情况"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AlarmCorrelationInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AlarmCorrelationInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            correlation = context.device_gateway.query_alarm_correlation(
                arguments.device_id, arguments.alarm_id
            )
        except Exception as exc:
            return failure_result(self.name, f"查询报警关联失败: {exc}")

        observation = (
            f"报警关联 {correlation.alarm_id} repeated={correlation.repeated_count}，"
            f"neighbor={correlation.neighbor_alarm_count}，pattern={correlation.pattern.value}，"
            f"告警风暴={correlation.is_burst}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ALARM_CORRELATION,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=correlation.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=correlation.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": correlation.device_id,
                "alarm_id": correlation.alarm_id,
                "is_burst": correlation.is_burst,
                "pattern": correlation.pattern.value,
            },
        )
