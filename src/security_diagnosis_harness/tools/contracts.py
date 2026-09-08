"""工具契约。

所有设备工具在 Phase 0 都只能是 READ_ONLY，且只能通过 ToolRegistry 调用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType, Reliability
from security_diagnosis_harness.ports.device_gateway import DeviceGateway


class ToolRiskLevel(StrEnum):
    """工具风险等级。

    Phase 0 只允许 `READ_ONLY`，写操作工具不允许注册。
    """

    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"


class ToolPermission(StrEnum):
    """工具所需权限。"""

    DEVICE_READ = "device:read"
    KNOWLEDGE_READ = "knowledge:read"


class ToolArgumentError(Exception):
    """工具参数非法。"""


class ToolBypassError(Exception):
    """工具被绕过 Registry 直接调用。"""


class ToolEvidenceDraft(BaseModel):
    """工具产生的证据草稿。

    草稿还没有 diagnosis_id，落到 `DiagnosisEvidence` 是应用服务的职责，
    Runner 不负责这一步。
    """

    model_config = ConfigDict(extra="forbid")

    evidence_type: EvidenceType
    source: EvidenceSource
    summary: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    reliability: Reliability = Reliability.MEDIUM
    redacted: bool = False


class ToolExecutionResult(BaseModel):
    """工具执行结果。

    失败时 `ok=False` 且 `evidence_drafts` 必须为空：工具失败不能被包装成证据。
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    ok: bool = True
    observation: str = ""
    error: str | None = None
    evidence_drafts: list[ToolEvidenceDraft] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionContext(BaseModel):
    """工具执行上下文。"""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    diagnosis_id: str = Field(min_length=1)
    fault_type: SecurityFaultType
    permissions: frozenset[ToolPermission] = frozenset()
    device_gateway: DeviceGateway | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    invoked_by_registry: bool = False


class BaseTool(ABC):
    """只读工具基类。

    子类只需要声明元信息、参数模型，并实现 `_execute`。
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""
    risk_level: ClassVar[ToolRiskLevel] = ToolRiskLevel.READ_ONLY
    required_permissions: ClassVar[frozenset[ToolPermission]] = frozenset()
    # None 表示支持所有故障类型。
    supported_fault_types: ClassVar[frozenset[SecurityFaultType] | None] = None
    input_model: ClassVar[type[BaseModel]]

    def parse_arguments(self, arguments: Mapping[str, Any]) -> BaseModel:
        """校验并解析参数。

        Raises:
            ToolArgumentError: 参数不是映射结构或不符合输入模型。
        """
        if not isinstance(arguments, Mapping):
            raise ToolArgumentError("工具参数必须是键值映射")
        try:
            return self.input_model(**arguments)
        except ValidationError as exc:
            raise ToolArgumentError(f"工具参数非法: {exc}") from exc

    def run(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """执行工具，只能由 Registry 调用。"""
        if not context.invoked_by_registry:
            raise ToolBypassError(f"工具 {self.name} 必须通过 ToolRegistry 调用")
        payload = self.parse_arguments(arguments)
        return self._execute(payload, context)

    @abstractmethod
    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """执行具体工具逻辑。"""


def failure_result(tool_name: str, error: str) -> ToolExecutionResult:
    """构造受控失败结果：不携带任何 EvidenceDraft。"""
    return ToolExecutionResult(tool_name=tool_name, ok=False, error=error)
