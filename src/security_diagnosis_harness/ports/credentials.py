"""凭证解析 Port；凭证只能在 Adapter 调用边界短暂存在。"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialResolverPort(Protocol):
    def resolve(self, credential_reference: str) -> str:
        """解析凭证引用；调用方不得记录返回值。"""
        ...

