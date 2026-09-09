"""recording__check_playback：只读检查指定时间段录像是否可回放。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

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

MAX_PLAYBACK_WINDOW_HOURS = 24 * 31


def _parse_iso_datetime(value: object) -> object:
    """把 ISO 时间字符串解析为 datetime，无法解析时抛 ValueError。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("时间参数必须是 ISO 8601 字符串")
    text = value.strip()
    if not text:
        raise ValueError("时间参数不能为空")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"无法解析 ISO 时间: {value}") from exc


IsoDatetime = Annotated[datetime, BeforeValidator(_parse_iso_datetime)]


class RecordingPlaybackInput(BaseModel):
    """recording__check_playback 参数。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    start_at: IsoDatetime
    end_at: IsoDatetime


class RecordingPlaybackTool(BaseTool):
    """检查指定时间段是否存在录像、是否可回放、失败原因是什么。"""

    name = "recording__check_playback"
    description = "检查指定时间段的录像是否存在且可回放"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = RecordingPlaybackInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, RecordingPlaybackInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        if arguments.end_at <= arguments.start_at:
            return failure_result(
                self.name,
                f"end_at 必须大于 start_at，"
                f"当前 start_at={arguments.start_at.isoformat()}，"
                f"end_at={arguments.end_at.isoformat()}",
            )

        window_hours = (arguments.end_at - arguments.start_at).total_seconds() / 3600
        if window_hours > MAX_PLAYBACK_WINDOW_HOURS:
            return failure_result(
                self.name,
                f"回放查询时间窗不能超过 {MAX_PLAYBACK_WINDOW_HOURS} 小时，"
                f"当前为 {window_hours:.1f} 小时",
            )

        try:
            result = context.device_gateway.check_recording_playback(
                arguments.device_id,
                arguments.channel_id,
                arguments.start_at,
                arguments.end_at,
            )
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"检查录像回放失败: {exc}")

        observation = (
            f"设备 {result.device_id} 通道 {result.channel_id} "
            f"{result.start_at.isoformat()} ~ {result.end_at.isoformat()} 回放状态="
            f"{result.status.value}，"
            f"文件数={result.file_count}，"
            f"可回放={result.playable}，"
            f"失败原因={result.failure_reason or '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.PLAYBACK_CHECK,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=result.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=result.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": result.device_id,
                "channel_id": result.channel_id,
                "playback_status": result.status.value,
                "playable": result.playable,
                "file_count": result.file_count,
            },
        )
