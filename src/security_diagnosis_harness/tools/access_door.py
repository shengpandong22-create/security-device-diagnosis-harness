"""access__query_door：只读查询门禁门状态。"""

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


class AccessDoorInput(BaseModel):
    """access__query_door 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    door_id: str = Field(min_length=1)


class AccessDoorTool(BaseTool):
    """查询门状态、门锁状态与门锁异常。"""

    name = "access__query_door"
    description = "查询门状态、门锁状态和门锁异常"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AccessDoorInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AccessDoorInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            door = context.device_gateway.query_door(arguments.device_id, arguments.door_id)
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询门状态失败: {exc}")

        observation = (
            f"门 {door.door_id} 当前状态={door.door_status.value}，"
            f"门锁状态={door.lock_status.value}，"
            f"门锁异常={door.has_lock_error}，"
            f"最近错误={door.last_error or '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ACCESS_DOOR,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=door.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=door.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": door.device_id,
                "door_id": door.door_id,
                "door_status": door.door_status.value,
                "lock_status": door.lock_status.value,
                "has_lock_error": door.has_lock_error,
            },
        )
