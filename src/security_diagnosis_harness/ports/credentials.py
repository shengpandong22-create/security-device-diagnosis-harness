"""凭证解析 Port；凭证只能在 Adapter 调用边界短暂存在。"""

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.device_integration import ResolvedCredential


@runtime_checkable
class CredentialResolverPort(Protocol):
    def resolve(self, credential_reference: str) -> ResolvedCredential:
        """解析凭证引用；调用方不得记录返回值。"""
        ...
