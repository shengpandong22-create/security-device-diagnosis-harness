"""access__query_policy：只读查询门禁授权策略。"""

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


class AccessPolicyInput(BaseModel):
    """access__query_policy 参数。"""

    model_config = ConfigDict(extra="forbid")

    person_id: str = Field(min_length=1)
    door_id: str = Field(min_length=1)


class AccessPolicyTool(BaseTool):
    """查询人员 / 凭证是否有指定门的权限和授权时段。"""

    name = "access__query_policy"
    description = "查询人员或凭证对指定门的授权策略"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AccessPolicyInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AccessPolicyInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            policy = context.device_gateway.query_access_policy(
                arguments.person_id,
                arguments.door_id,
            )
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询门禁授权策略失败: {exc}")

        observation = (
            f"门 {policy.door_id} 授权允许={policy.allowed}，"
            f"授权时段数={len(policy.time_ranges)}，"
            f"跨天时段数={len(policy.crossing_time_ranges)}，"
            f"有效期={policy.valid_from.isoformat() if policy.valid_from else '-'}"
            f" ~ {policy.valid_until.isoformat() if policy.valid_until else '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ACCESS_POLICY,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=policy.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=policy.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": policy.device_id,
                "door_id": policy.door_id,
                "allowed": policy.allowed,
                "time_range_count": len(policy.time_ranges),
            },
        )
