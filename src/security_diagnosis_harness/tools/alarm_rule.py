"""alarm__query_rule：只读查询报警规则配置。"""

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


class AlarmRuleInput(BaseModel):
    """alarm__query_rule 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)


class AlarmRuleTool(BaseTool):
    """查询报警规则类型、灵敏度、阈值、防抖时间与布防时段。"""

    name = "alarm__query_rule"
    description = "查询报警规则配置"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AlarmRuleInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AlarmRuleInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            rule = context.device_gateway.query_alarm_rule(
                arguments.device_id, arguments.rule_id
            )
        except Exception as exc:
            return failure_result(self.name, f"查询报警规则失败: {exc}")

        observation = (
            f"报警规则 {rule.rule_id} 类型={rule.alarm_type.value}，"
            f"启用={rule.enabled}，灵敏度={rule.sensitivity.value}，"
            f"阈值={rule.threshold}，防抖={rule.debounce_seconds}s，"
            f"疑似过敏={rule.is_over_sensitive}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ALARM_RULE,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=rule.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=rule.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": rule.device_id,
                "rule_id": rule.rule_id,
                "alarm_type": rule.alarm_type.value,
                "over_sensitive": rule.is_over_sensitive,
            },
        )
