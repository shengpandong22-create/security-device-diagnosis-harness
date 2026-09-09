"""摄像头黑屏候选根因规则。

这是一层确定性辅助判断，不是 LLM 的替代品：

- 输入是当前诊断已经落地的 Evidence；
- 输出只是"候选标签 + 证据链解释 + 排查顺序 + 排除项"；
- 它永远不会直接产生 confirmed，也不绕过 CitationPolicy。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.citation_policy import DEVICE_FACT_EVIDENCE_TYPES
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence, EvidenceType

# 编码配置过高的判定阈值。
HIGH_BITRATE_KBPS: int = 4096
HIGH_RESOLUTION_PIXELS: int = 2_500_000

# 告警类型常量。
ENCODER_TIMEOUT_ALARM = "ENCODER_TIMEOUT"

_FAILED_PULL_STATUSES = {"failed", "timeout"}


class CameraDiagnosisLabel(StrEnum):
    """摄像头黑屏候选根因标签。"""

    DEVICE_OFFLINE_OR_NETWORK_UNREACHABLE = "device_offline_or_network_unreachable"
    CHANNEL_BINDING_OR_PLATFORM_ACCESS_ISSUE = "channel_binding_or_platform_access_issue"
    STREAM_PUBLISH_OR_ENCODER_ISSUE = "stream_publish_or_encoder_issue"
    OVERLOADED_ENCODING_CONFIGURATION = "overloaded_encoding_configuration"
    PLATFORM_PULL_OR_ACCESS_PATH_ISSUE = "platform_pull_or_access_path_issue"
    INSUFFICIENT_CAMERA_FACTS = "insufficient_camera_facts"


LABEL_EXPLANATIONS: dict[CameraDiagnosisLabel, str] = {
    CameraDiagnosisLabel.DEVICE_OFFLINE_OR_NETWORK_UNREACHABLE: (
        "设备离线或网络不可达，设备侧无法提供任何码流"
    ),
    CameraDiagnosisLabel.CHANNEL_BINDING_OR_PLATFORM_ACCESS_ISSUE: (
        "通道绑定、通道状态或平台接入异常"
    ),
    CameraDiagnosisLabel.STREAM_PUBLISH_OR_ENCODER_ISSUE: "码流发布失败或编码器异常",
    CameraDiagnosisLabel.OVERLOADED_ENCODING_CONFIGURATION: (
        "编码配置过高导致设备编码压力过大"
    ),
    CameraDiagnosisLabel.PLATFORM_PULL_OR_ACCESS_PATH_ISSUE: "平台侧拉流或接入链路异常",
    CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS: "摄像头事实不足，无法给出可靠根因候选",
}

TROUBLESHOOTING_ORDER: dict[CameraDiagnosisLabel, list[str]] = {
    CameraDiagnosisLabel.DEVICE_OFFLINE_OR_NETWORK_UNREACHABLE: [
        "确认设备供电与物理连接",
        "确认设备到平台的网络连通性",
        "确认设备心跳与注册状态",
        "恢复后重新观察通道与码流状态",
    ],
    CameraDiagnosisLabel.CHANNEL_BINDING_OR_PLATFORM_ACCESS_ISSUE: [
        "确认通道是否绑定了正确摄像头",
        "确认通道在平台侧是否完成注册",
        "确认通道号与设备能力是否匹配",
        "重新绑定后复测取流",
    ],
    CameraDiagnosisLabel.STREAM_PUBLISH_OR_ENCODER_ISSUE: [
        "确认主码流发布状态与错误码",
        "确认编码器是否异常或超时",
        "对比子码流是否正常",
        "必要时重启编码模块（需人工在运维流程中执行）",
    ],
    CameraDiagnosisLabel.OVERLOADED_ENCODING_CONFIGURATION: [
        "核对分辨率、帧率与码率是否超出设备能力",
        "临时降低码率或分辨率后复测",
        "确认设备 CPU 与编码资源占用",
        "确认设备固件版本是否存在已知编码问题",
    ],
    CameraDiagnosisLabel.PLATFORM_PULL_OR_ACCESS_PATH_ISSUE: [
        "确认平台侧拉流任务与接入路径",
        "确认平台到设备的网络与端口可达",
        "确认设备侧鉴权信息是否过期（使用脱敏配置核对）",
        "在平台侧重试拉流并观察错误码",
    ],
    CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS: [
        "补充采集设备状态、通道、码流与平台拉流事实",
        "确认只读工具是否全部执行成功",
        "补充现场描述后重新运行诊断",
    ],
}


class CameraFacts(BaseModel):
    """从 Evidence 中提取出的摄像头事实。"""

    model_config = ConfigDict(extra="forbid")

    has_device_status: bool = False
    has_channel: bool = False
    has_stream: bool = False
    has_platform_pull: bool = False
    online: bool | None = None
    device_stream_status: str | None = None
    channel_status: str | None = None
    channel_bound: bool | None = None
    platform_registered: bool | None = None
    stream_pull_status: str | None = None
    stream_error_code: str | None = None
    platform_pull_status: str | None = None
    platform_error_code: str | None = None
    bitrate_kbps: int | None = None
    resolution: str | None = None
    alarm_types: list[str] = Field(default_factory=list)
    device_fact_types: list[str] = Field(default_factory=list)

    @property
    def has_any_camera_fact(self) -> bool:
        return any(
            [self.has_device_status, self.has_channel, self.has_stream, self.has_platform_pull]
        )


class CameraDiagnosisRuleResult(BaseModel):
    """候选根因规则的输出。"""

    model_config = ConfigDict(extra="forbid")

    label: CameraDiagnosisLabel
    explanation: str
    evidence_chain: list[str] = Field(default_factory=list)
    excluded_candidates: list[str] = Field(default_factory=list)
    troubleshooting_order: list[str] = Field(default_factory=list)
    matched_rule: str = ""
    device_fact_type_count: int = 0


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _resolution_pixels(resolution: str | None) -> int:
    """把 "2560x1440" 解析为像素数；无法解析时返回 0。"""
    if not resolution:
        return 0
    try:
        width_text, _, height_text = resolution.lower().partition("x")
        return int(width_text.strip()) * int(height_text.strip())
    except (TypeError, ValueError):
        return 0


def is_high_encoding_load(bitrate_kbps: int | None, resolution: str | None) -> bool:
    """判断编码配置是否明显偏高。"""
    if bitrate_kbps is not None and bitrate_kbps >= HIGH_BITRATE_KBPS:
        return True
    return _resolution_pixels(resolution) >= HIGH_RESOLUTION_PIXELS


def extract_camera_facts(evidence: list[DiagnosisEvidence]) -> CameraFacts:
    """从 Evidence 列表中提取摄像头事实。"""
    facts = CameraFacts()
    alarm_types: list[str] = []
    device_fact_types: list[str] = []

    for item in evidence:
        payload = item.payload or {}
        if item.evidence_type in DEVICE_FACT_EVIDENCE_TYPES:
            device_fact_types.append(item.evidence_type.value)

        if item.evidence_type is EvidenceType.DEVICE_STATUS:
            facts.has_device_status = True
            facts.online = _as_bool(payload.get("online"))
            facts.device_stream_status = _as_str(payload.get("stream_status"))
        elif item.evidence_type is EvidenceType.DEVICE_CHANNEL:
            facts.has_channel = True
            facts.channel_status = _as_str(payload.get("channel_status"))
            facts.channel_bound = _as_bool(payload.get("bound"))
            facts.platform_registered = _as_bool(payload.get("platform_registered"))
        elif item.evidence_type is EvidenceType.DEVICE_STREAM:
            facts.has_stream = True
            facts.stream_pull_status = _as_str(payload.get("pull_status"))
            facts.stream_error_code = _as_str(payload.get("error_code"))
            facts.bitrate_kbps = _as_int(payload.get("bitrate_kbps"))
            facts.resolution = _as_str(payload.get("resolution"))
        elif item.evidence_type is EvidenceType.PLATFORM_PULL:
            facts.has_platform_pull = True
            facts.platform_pull_status = _as_str(payload.get("pull_status"))
            facts.platform_error_code = _as_str(payload.get("error_code"))
        elif item.evidence_type is EvidenceType.DEVICE_ALARM:
            events = payload.get("events") or []
            for event in events:
                event_type = _as_str(event.get("event_type")) if isinstance(event, dict) else None
                if event_type:
                    alarm_types.append(event_type)
        elif item.evidence_type is EvidenceType.DEVICE_CONFIG:
            if facts.bitrate_kbps is None:
                facts.bitrate_kbps = _as_int(payload.get("bitrate_kbps"))
            if not facts.resolution:
                facts.resolution = _as_str(payload.get("resolution"))

    facts.alarm_types = alarm_types
    facts.device_fact_types = sorted(set(device_fact_types))
    return facts


def _build_result(
    label: CameraDiagnosisLabel,
    facts: CameraFacts,
    matched_rule: str,
    evidence_chain: list[str],
) -> CameraDiagnosisRuleResult:
    excluded = [
        f"{other.value}：{explanation}"
        for other, explanation in LABEL_EXPLANATIONS.items()
        if other is not label
    ]
    return CameraDiagnosisRuleResult(
        label=label,
        explanation=LABEL_EXPLANATIONS[label],
        evidence_chain=evidence_chain,
        excluded_candidates=excluded,
        troubleshooting_order=list(TROUBLESHOOTING_ORDER[label]),
        matched_rule=matched_rule,
        device_fact_type_count=len(facts.device_fact_types),
    )


def infer_camera_black_screen_label(
    evidence: list[DiagnosisEvidence],
) -> CameraDiagnosisRuleResult:
    """根据 Evidence 推断摄像头黑屏的候选根因标签。

    规则只输出候选，不产生 confirmed，也不修改任何诊断状态。
    """
    facts = extract_camera_facts(evidence)

    if not facts.has_any_camera_fact:
        return _build_result(
            CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS,
            facts,
            "R0 缺少任何摄像头设备事实",
            ["没有采集到设备状态、通道、码流或平台拉流事实"],
        )

    chain: list[str] = []
    if facts.online is not None:
        chain.append(f"设备在线状态={facts.online}")
    if facts.channel_status is not None:
        chain.append(f"通道状态={facts.channel_status}")
    if facts.platform_registered is not None:
        chain.append(f"平台已注册={facts.platform_registered}")
    if facts.stream_pull_status is not None:
        chain.append(f"码流取流={facts.stream_pull_status}")
    if facts.platform_pull_status is not None:
        chain.append(f"平台拉流={facts.platform_pull_status}")
    if facts.bitrate_kbps is not None:
        chain.append(f"码率={facts.bitrate_kbps}kbps")
    if facts.resolution:
        chain.append(f"分辨率={facts.resolution}")
    if facts.alarm_types:
        chain.append(f"告警={','.join(sorted(set(facts.alarm_types)))}")

    # 规则 1：设备离线。
    if facts.online is False:
        return _build_result(
            CameraDiagnosisLabel.DEVICE_OFFLINE_OR_NETWORK_UNREACHABLE,
            facts,
            "R1 设备离线",
            chain,
        )

    # 规则 2：设备在线但通道离线或未接入平台。
    if facts.online is True and (
        facts.channel_status == "offline" or facts.platform_registered is False
    ):
        return _build_result(
            CameraDiagnosisLabel.CHANNEL_BINDING_OR_PLATFORM_ACCESS_ISSUE,
            facts,
            "R2 通道离线或未接入平台",
            chain,
        )

    # 规则 3：编码配置过高（比通用码流规则更具体，因此先判定）。
    has_encoder_timeout = ENCODER_TIMEOUT_ALARM in facts.alarm_types or (
        facts.stream_error_code == ENCODER_TIMEOUT_ALARM
    )
    if has_encoder_timeout and is_high_encoding_load(facts.bitrate_kbps, facts.resolution):
        return _build_result(
            CameraDiagnosisLabel.OVERLOADED_ENCODING_CONFIGURATION,
            facts,
            "R3 编码配置过高 + 编码器超时",
            chain,
        )

    # 规则 4：码流发布或编码异常。
    stream_failed = (facts.stream_pull_status or "") in _FAILED_PULL_STATUSES or (
        facts.device_stream_status == "abnormal"
    )
    if stream_failed:
        return _build_result(
            CameraDiagnosisLabel.STREAM_PUBLISH_OR_ENCODER_ISSUE,
            facts,
            "R4 码流发布失败或编码异常",
            chain,
        )

    # 规则 5：设备侧正常但平台拉流失败。
    if (facts.platform_pull_status or "") in _FAILED_PULL_STATUSES:
        return _build_result(
            CameraDiagnosisLabel.PLATFORM_PULL_OR_ACCESS_PATH_ISSUE,
            facts,
            "R5 平台拉流失败或超时",
            chain,
        )

    # 规则 6：事实不足以支撑任何根因候选。
    return _build_result(
        CameraDiagnosisLabel.INSUFFICIENT_CAMERA_FACTS,
        facts,
        "R6 事实不足",
        chain or ["采集到的事实不足以区分根因候选"],
    )
