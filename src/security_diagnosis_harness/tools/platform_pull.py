"""platform__query_pull_status：只读查询平台侧拉流状态。"""

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


class PlatformPullInput(BaseModel):
    """platform__query_pull_status 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)


class PlatformPullStatusTool(BaseTool):
    """查询平台侧对该设备的拉流结果、错误码与最近失败时间。"""

    name = "platform__query_pull_status"
    description = "查询平台侧拉流状态与错误码"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = PlatformPullInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, PlatformPullInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            pull = context.device_gateway.query_platform_pull_status(arguments.device_id)
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询平台拉流状态失败: {exc}")

        observation = (
            f"平台 {pull.platform} 对设备 {pull.device_id} 拉流={pull.pull_status.value}，"
            f"错误码={pull.error_code or '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.PLATFORM_PULL,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=pull.model_dump(mode="json"),
            reliability=Reliability.MEDIUM,
            redacted=pull.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={"device_id": pull.device_id, "pull_status": pull.pull_status.value},
        )
