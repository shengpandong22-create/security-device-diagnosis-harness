"""storage__query_status：只读查询录像存储状态。"""

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


class StorageStatusInput(BaseModel):
    """storage__query_status 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)


class StorageStatusTool(BaseTool):
    """查询录像存储池状态、容量与最近错误。"""

    name = "storage__query_status"
    description = "查询录像存储池状态、剩余容量与最近错误"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = StorageStatusInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, StorageStatusInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            storage = context.device_gateway.query_storage_status(
                arguments.device_id,
                arguments.channel_id,
            )
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询录像存储状态失败: {exc}")

        free_percent = storage.free_percent
        observation = (
            f"存储 {storage.storage_id} 状态={storage.status.value}，"
            f"总容量={storage.total_gb}GB，"
            f"剩余={storage.free_gb}GB，"
            f"剩余比例={free_percent if free_percent is not None else '-'}%，"
            f"容量不足={storage.is_capacity_low}，"
            f"可用={storage.is_available}，"
            f"最近错误={storage.last_error or '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.STORAGE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=storage.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=storage.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "storage_id": storage.storage_id,
                "storage_status": storage.status.value,
                "is_capacity_low": storage.is_capacity_low,
            },
        )
