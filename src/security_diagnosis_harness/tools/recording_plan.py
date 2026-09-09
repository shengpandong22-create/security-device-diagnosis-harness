"""recording__query_plan：只读查询通道录像计划。"""

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


class RecordingPlanInput(BaseModel):
    """recording__query_plan 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)


class RecordingPlanTool(BaseTool):
    """查询录像计划是否启用、录像模式与计划时间段。"""

    name = "recording__query_plan"
    description = "查询通道录像计划的状态、模式与计划时间段"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = RecordingPlanInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, RecordingPlanInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            plan = context.device_gateway.query_recording_plan(
                arguments.device_id,
                arguments.channel_id,
            )
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询录像计划失败: {exc}")

        gap_hint = "，计划启用但无有效时间段" if plan.has_schedule_gap else ""
        observation = (
            f"设备 {plan.device_id} 通道 {plan.channel_id} 录像计划 "
            f"状态={plan.status.value}，"
            f"模式={plan.mode.value}，"
            f"时间段数={len(plan.time_ranges)}"
            f"（跨天 {len(plan.crossing_time_ranges)} 段），"
            f"计划生效={plan.is_plan_active}，"
            f"保留天数={plan.retention_days}"
            f"{gap_hint}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.RECORDING_PLAN,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=plan.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=plan.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": plan.device_id,
                "channel_id": plan.channel_id,
                "plan_status": plan.status.value,
                "has_schedule_gap": plan.has_schedule_gap,
            },
        )
