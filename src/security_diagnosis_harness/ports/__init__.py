"""Ports：与具体供应商、具体设备平台解耦的抽象契约。"""

from security_diagnosis_harness.ports.audit_repository import AuditRepository
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGateway,
    DeviceGatewayDataError,
    DeviceGatewayError,
    DeviceNotFoundError,
)
from security_diagnosis_harness.ports.diagnosis_repository import DiagnosisRepository
from security_diagnosis_harness.ports.llm import (
    ChatMessage,
    ChatRole,
    ConclusionDraft,
    FinishReason,
    LLMClient,
    LLMRequest,
    LLMResponse,
    ToolCall,
)

__all__ = [
    "AuditRepository",
    "ChatMessage",
    "ChatRole",
    "ConclusionDraft",
    "DeviceGateway",
    "DeviceGatewayDataError",
    "DeviceGatewayError",
    "DeviceNotFoundError",
    "DiagnosisRepository",
    "FinishReason",
    "LLMClient",
    "LLMRequest",
    "LLMResponse",
    "ToolCall",
]
