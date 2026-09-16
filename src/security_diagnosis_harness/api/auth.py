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
        self._credentials = (
            (sha256(token.encode("utf-8")).digest(), ApiPrincipal(actor=actor, roles=roles)),
        )

    @classmethod
    def for_separated_duties(
        cls,
        *,
        operator_token: str,
        operator_actor: str,
        reviewer_token: str,
        reviewer_actor: str,
    ) -> BearerAuthenticator:
        """构造正式双凭证认证器，并拒绝 token 或 actor 复用。"""
        values = (operator_token, operator_actor, reviewer_token, reviewer_actor)
        if any(not value.strip() for value in values):
            raise ValueError("operator/reviewer token 与 actor 均不能为空")
        if compare_digest(operator_token, reviewer_token):
            raise ValueError("operator 与 reviewer 必须使用不同 token")
        if operator_actor == reviewer_actor:
            raise ValueError("operator 与 reviewer 必须是不同 actor")
        instance = cls.__new__(cls)
        instance._credentials = (
            (
                sha256(operator_token.encode("utf-8")).digest(),
                ApiPrincipal(actor=operator_actor, roles=frozenset({"operator"})),
            ),
            (
                sha256(reviewer_token.encode("utf-8")).digest(),
                ApiPrincipal(actor=reviewer_actor, roles=frozenset({"reviewer"})),
            ),
        )
        return instance

    def authorize(self, authorization: str | None, required_role: str) -> ApiPrincipal:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication_required")
        supplied = authorization.removeprefix("Bearer ").strip()
        supplied_digest = sha256(supplied.encode("utf-8")).digest()
        principal = next(
            (
                principal
                for digest, principal in self._credentials
                if supplied and compare_digest(supplied_digest, digest)
            ),
            None,
        )
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication_failed")
        if required_role not in principal.roles:
            raise HTTPException(status_code=403, detail="permission_denied")
        return principal

    def __repr__(self) -> str:
        return "BearerAuthenticator(token=<redacted>)"


__all__ = ["ApiPrincipal", "BearerAuthenticator"]
