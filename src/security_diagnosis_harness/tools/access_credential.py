"""access__query_credential：只读查询门禁凭证状态。"""

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


class AccessCredentialInput(BaseModel):
    """access__query_credential 参数。"""

    model_config = ConfigDict(extra="forbid")

    credential_id: str = Field(min_length=1)


class AccessCredentialTool(BaseTool):
    """查询卡、人脸、二维码、指纹等凭证是否有效、冻结或过期。"""

    name = "access__query_credential"
    description = "查询门禁凭证类型、状态和有效期"
    risk_level = ToolRiskLevel.READ_ONLY
    required_permissions = frozenset({ToolPermission.DEVICE_READ})
    input_model = AccessCredentialInput

    def _execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert isinstance(arguments, AccessCredentialInput)
        if context.device_gateway is None:
            return failure_result(self.name, "未配置 DeviceGateway")

        try:
            credential = context.device_gateway.query_credential(arguments.credential_id)
        except Exception as exc:  # 网关异常转成受控失败，不伪造成证据
            return failure_result(self.name, f"查询门禁凭证失败: {exc}")

        observation = (
            f"凭证类型={credential.credential_type.value}，"
            f"凭证状态={credential.status.value}，"
            f"凭证有效={credential.is_valid}，"
            f"过期时间={credential.expires_at.isoformat() if credential.expires_at else '-'}"
        )
        draft = ToolEvidenceDraft(
            evidence_type=EvidenceType.ACCESS_CREDENTIAL,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary=observation,
            payload=credential.model_dump(mode="json"),
            reliability=Reliability.HIGH,
            redacted=credential.redacted,
        )
        return ToolExecutionResult(
            tool_name=self.name,
            observation=observation,
            evidence_drafts=[draft],
            metadata={
                "device_id": credential.device_id,
                "credential_type": credential.credential_type.value,
                "credential_status": credential.status.value,
                "credential_valid": credential.is_valid,
            },
        )
