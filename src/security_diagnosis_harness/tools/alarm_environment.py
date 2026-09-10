"""alarm__query_environment：只读查询报警环境干扰。"""

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


class AlarmEnvironmentInput(BaseModel):
    """alarm__query_environment 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)


class AlarmEnvironmentTool(BaseTool):
    """查询雨、雾、强光、风、夜间、阴影等环境干扰事实。"""

    name = "alarm__query_environment"
    description = "查询报警触发时的环境干扰事实"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AlarmEnvironmentInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AlarmEnvironmentInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            environment = context.device_gateway.query_alarm_environment(
                arguments.device_id, arguments.alarm_id
            )
        except Exception as exc:
            return failure_result(self.name, f"查询报警环境失败: {exc}")

        interferences = [item.value for item in environment.interference_types]
        observation = (
            f"报警环境 {environment.alarm_id} 干扰={','.join(interferences) or '-'}，"
            f"可见度={environment.visibility or '-'}，"
            f"照度={_display_optional(environment.illumination_lux)}，"
            f"风速={_display_optional(environment.wind_speed)}，"
            f"存在干扰={environment.has_interference}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ALARM_ENVIRONMENT,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=environment.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=environment.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": environment.device_id,
                "alarm_id": environment.alarm_id,
                "has_interference": environment.has_interference,
            },
        )


def _display_optional(value: object) -> object:
    return value if value is not None else "-"
