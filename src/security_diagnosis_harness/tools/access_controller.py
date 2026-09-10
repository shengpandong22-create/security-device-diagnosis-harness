"""access__query_controller：只读查询门禁控制器状态。"""

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


class AccessControllerInput(BaseModel):
    """access__query_controller 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)


class AccessControllerTool(BaseTool):
    """查询门禁控制器在线状态、健康状态与最近错误。"""

    name = "access__query_controller"
    description = "查询门禁控制器在线状态、健康状态和最近错误"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AccessControllerInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AccessControllerInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            controller = context.device_gateway.query_access_controller(arguments.device_id)
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询门禁控制器失败: {exc}")

        observation = (
            f"门禁控制器 {controller.controller_id} "
            f"在线状态={controller.status.value}，"
            f"健康状态={controller.health.value}，"
            f"最近错误={controller.last_error or '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ACCESS_CONTROLLER,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=controller.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=controller.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": controller.device_id,
                "controller_id": controller.controller_id,
                "controller_status": controller.status.value,
                "controller_health": controller.health.value,
            },
        )
