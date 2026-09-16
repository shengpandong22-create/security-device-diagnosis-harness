"""正式 API 的最小 Bearer 身份认证与角色授权。"""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class ApiPrincipal(BaseModel):
    """由可信认证上下文注入的调用方身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor: str = Field(min_length=1, max_length=80)
    roles: frozenset[str] = frozenset()


class BearerAuthenticator:
    """只保留 token SHA-256 摘要，不保存或输出明文 token。"""

    def __init__(self, token: str, *, actor: str, roles: frozenset[str]) -> None:
        if not token.strip() or not actor.strip():
            raise ValueError("API token 与 actor 不能为空")
        self._token_digest = sha256(token.encode("utf-8")).digest()
        self._principal = ApiPrincipal(actor=actor, roles=roles)

    def authorize(self, authorization: str | None, required_role: str) -> ApiPrincipal:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication_required")
        supplied = authorization.removeprefix("Bearer ").strip()
        supplied_digest = sha256(supplied.encode("utf-8")).digest()
        if not supplied or not compare_digest(supplied_digest, self._token_digest):
            raise HTTPException(status_code=401, detail="authentication_failed")
        if required_role not in self._principal.roles:
            raise HTTPException(status_code=403, detail="permission_denied")
        return self._principal

    def __repr__(self) -> str:
        return "BearerAuthenticator(token=<redacted>)"


__all__ = ["ApiPrincipal", "BearerAuthenticator"]
