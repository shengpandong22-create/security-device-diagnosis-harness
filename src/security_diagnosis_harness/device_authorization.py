"""Phase 10B-1：授权清单与调用前预算预检（纯领域 / 应用级决策核心）。

任何未来的真实设备调用都必须先经过本模块拿到 allow / deny 判定，默认拒绝。
本模块刻意保持纯函数式与离线：

- 不导入 os / socket / httpx / requests，不读取环境变量或 `.env`，不访问网络、
  数据库或文件；
- 不接入真实 HTTP Adapter，不修改正式 Runtime，也不提前进入 Phase 10C；
- 不做自动重试、自动扩容预算、自动通知或任何设备写操作；
- 每一种失败都返回强类型 deny reason，不拼接异常原文，也不回显可疑凭证内容。

时序边界语义固定为半开区间：``valid_from <= now < valid_until``。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _FrozenDict(dict):
    """可序列化的只读字典，避免 ``mappingproxy`` 与 Pydantic 深拷贝冲突。"""

    def _immutable(self, *_args, **_kwargs) -> None:
        raise TypeError("authorization mapping is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, _memo):
        return self


class DeviceReadOperation(StrEnum):
    """已批准的**只读**设备操作 allowlist（取值即 ``DeviceGateway`` 方法名）。

    这里刻意使用精确枚举而不是对方法名做 ``query/read/check/search`` 子串判断，
    以免形如 ``read_everything`` 的写操作被误判为只读。
    """

    QUERY_STATUS = "query_status"
    QUERY_CHANNEL_SNAPSHOT = "query_channel_snapshot"
    QUERY_STREAM_SNAPSHOT = "query_stream_snapshot"
    QUERY_PLATFORM_PULL_STATUS = "query_platform_pull_status"
    QUERY_RECORDING_PLAN = "query_recording_plan"
    QUERY_STORAGE_STATUS = "query_storage_status"
    CHECK_RECORDING_PLAYBACK = "check_recording_playback"
    QUERY_ACCESS_CONTROLLER = "query_access_controller"
    QUERY_DOOR = "query_door"
    QUERY_CREDENTIAL = "query_credential"
    QUERY_ACCESS_POLICY = "query_access_policy"
    SEARCH_ACCESS_EVENTS = "search_access_events"
    QUERY_ALARM_RULE = "query_alarm_rule"
    QUERY_ALARM_SIGNAL = "query_alarm_signal"
    QUERY_ALARM_ENVIRONMENT = "query_alarm_environment"
    QUERY_ALARM_VERIFICATION = "query_alarm_verification"
    QUERY_ALARM_CORRELATION = "query_alarm_correlation"
    SEARCH_ALARM_EVENTS = "search_alarm_events"
    READ_CONFIG_SNAPSHOT = "read_config_snapshot"


#: 唯一允许出现在授权清单中的操作集合。清单校验显式与此集合取子集，
#: 以便未来枚举被追加写操作时立即失败，而不是静默放行。
READ_ONLY_OPERATIONS: frozenset[DeviceReadOperation] = frozenset(DeviceReadOperation)


class AuthorizationDenyReason(StrEnum):
    """稳定的调用前拒绝原因码；不携带任何外部文本。"""

    MISSING_MANIFEST = "missing_manifest"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"
    ASSET_OUT_OF_SCOPE = "asset_out_of_scope"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    TOTAL_BUDGET_EXHAUSTED = "total_budget_exhausted"
    OPERATION_BUDGET_EXHAUSTED = "operation_budget_exhausted"


# 不透明凭证引用：`<namespace>:<name>`，两段都禁止 `.` `/` `@` `:` `=` `?` `&`，
# 因此天然排除 URL、userinfo、主机 / IP 与查询串凭证。
_CREDENTIAL_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9_-]{0,63}$",
    re.ASCII,
)

# 环境别名与资产别名：允许 `.` 以便站点层级别名，反向由 IP 字面量检查兜住。
_ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$", re.ASCII)

_IPV4_LITERAL_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

# 命中即视为疑似明文凭证；只做子串判定，绝不回显原值。
_FORBIDDEN_CREDENTIAL_MARKERS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "private_key",
    "private-key",
    "bearer",
)


def _contains_credential_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _FORBIDDEN_CREDENTIAL_MARKERS)


def _validate_alias(value: str, label: str) -> str:
    """校验别名：只允许受控别名，禁止 URL、userinfo、IP 与凭证字样。"""
    if not isinstance(value, str) or not _ALIAS_PATTERN.fullmatch(value):
        raise ValueError(f"{label} 只能是受控别名（小写字母开头，仅含 a-z 0-9 . _ -）")
    if _IPV4_LITERAL_PATTERN.search(value) is not None:
        raise ValueError(f"{label} 不能是 IP 字面量")
    if _contains_credential_marker(value):
        raise ValueError(f"{label} 不能包含凭证字样")
    return value


def _validate_credential_reference(value: str) -> str:
    """校验凭证引用为纯不透明引用，防止其变成秘密存储后门。"""
    if not isinstance(value, str) or not _CREDENTIAL_REFERENCE_PATTERN.fullmatch(value):
        raise ValueError(
            "credential_ref 必须是不透明引用（namespace:name，禁止 URL、userinfo、IP 与明文凭证）"
        )
    if _contains_credential_marker(value):
        raise ValueError("credential_ref 不能包含凭证字样")
    return value


def _require_aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} 必须包含时区")
    return value


class AuthorizationManifest(BaseModel):
    """不可变授权清单。

    清单本身只是“被授权”的**声明**，不等于当前调用已被授权：是否放行完全由
    :func:`preflight_device_call` 依据时间窗、资产范围、操作范围与预算重新判定。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    manifest_id: str = Field(min_length=1)
    environment_alias: str
    asset_scope_aliases: frozenset[str]
    credential_ref: str
    valid_from: datetime
    valid_until: datetime
    allowed_operations: frozenset[DeviceReadOperation]
    max_total_calls: int = Field(ge=0)
    max_calls_per_operation: Mapping[DeviceReadOperation, int]

    @field_validator("environment_alias")
    @classmethod
    def _validate_environment_alias(cls, value: str) -> str:
        return _validate_alias(value, "environment_alias")

    @field_validator("asset_scope_aliases", mode="after")
    @classmethod
    def _validate_asset_scope(cls, value: frozenset[str]) -> frozenset[str]:
        return frozenset(_validate_alias(alias, "asset_scope_aliases") for alias in value)

    @field_validator("credential_ref")
    @classmethod
    def _validate_credential_ref(cls, value: str) -> str:
        return _validate_credential_reference(value)

    @field_validator("valid_from", "valid_until")
    @classmethod
    def _validate_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "授权时间窗")

    @field_validator("max_calls_per_operation", mode="after")
    @classmethod
    def _freeze_calls_per_operation(
        cls,
        value: Mapping[DeviceReadOperation, int],
    ) -> Mapping[DeviceReadOperation, int]:
        frozen = dict(value)
        for count in frozen.values():
            if count < 0:
                raise ValueError("max_calls_per_operation 的预算不能为负")
        return _FrozenDict(frozen)

    @model_validator(mode="after")
    def _validate_consistency(self) -> AuthorizationManifest:
        if self.valid_from >= self.valid_until:
            raise ValueError("valid_from 必须早于 valid_until")
        if not self.allowed_operations <= READ_ONLY_OPERATIONS:
            raise ValueError("allowed_operations 只能包含只读操作")
        if not self.allowed_operations <= set(self.max_calls_per_operation):
            raise ValueError("每个 allowed_operations 都必须显式声明单操作预算")
        return self


class AuthorizationRequest(BaseModel):
    """一次待授权设备调用的不可变请求快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    request_id: str = Field(min_length=1)
    environment_alias: str
    asset_alias: str
    operation: DeviceReadOperation
    requested_at: datetime

    @field_validator("environment_alias")
    @classmethod
    def _validate_environment_alias(cls, value: str) -> str:
        return _validate_alias(value, "environment_alias")

    @field_validator("asset_alias")
    @classmethod
    def _validate_asset_alias(cls, value: str) -> str:
        return _validate_alias(value, "asset_alias")

    @field_validator("requested_at")
    @classmethod
    def _validate_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "requested_at")


class AuthorizationBudgetState(BaseModel):
    """不可变预算状态；总计数与单操作计数在构造时保持一致。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_calls_consumed: int = Field(default=0, ge=0)
    # Pydantic deep-copies plain defaults. ``mappingproxy`` cannot be pickled, so
    # start from a fresh dict and freeze it in the field validator below.
    calls_by_operation: Mapping[DeviceReadOperation, int] = Field(default_factory=dict)

    @field_validator("calls_by_operation", mode="after")
    @classmethod
    def _freeze_calls(
        cls,
        value: Mapping[DeviceReadOperation, int],
    ) -> Mapping[DeviceReadOperation, int]:
        frozen = dict(value)
        for count in frozen.values():
            if count < 0:
                raise ValueError("calls_by_operation 的计数不能为负")
        return _FrozenDict(frozen)

    @model_validator(mode="after")
    def _validate_total_is_atomic_sum(self) -> AuthorizationBudgetState:
        if self.total_calls_consumed != sum(self.calls_by_operation.values()):
            raise ValueError("total_calls_consumed 必须等于各操作计数之和")
        return self

    @classmethod
    def from_consumed(
        cls,
        calls_by_operation: Mapping[DeviceReadOperation, int],
    ) -> AuthorizationBudgetState:
        """由单操作计数构造一致状态，总计数自动求和。"""
        frozen = dict(calls_by_operation)
        return cls(
            total_calls_consumed=sum(frozen.values()),
            calls_by_operation=frozen,
        )

    def consumed_for(self, operation: DeviceReadOperation) -> int:
        """返回某操作的已消费次数；未出现过的操作按 0 计。"""
        return self.calls_by_operation.get(operation, 0)


class AuthorizationDecision(BaseModel):
    """调用前预检结果；``allowed`` 与 ``deny_reason`` 互斥。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    deny_reason: AuthorizationDenyReason | None = None
    manifest_id: str | None = None
    budget_state: AuthorizationBudgetState

    @model_validator(mode="after")
    def _validate_decision(self) -> AuthorizationDecision:
        if self.allowed:
            if self.deny_reason is not None:
                raise ValueError("allow 结果不得携带 deny_reason")
            if self.manifest_id is None:
                raise ValueError("allow 结果必须绑定授权清单")
        elif self.deny_reason is None:
            raise ValueError("deny 结果必须携带强类型 deny_reason")
        return self


def preflight_device_call(
    manifest: AuthorizationManifest | None,
    request: AuthorizationRequest,
    budget_state: AuthorizationBudgetState,
) -> AuthorizationDecision:
    """调用前预检：默认拒绝，只有全部条件成立才放行并消费一次预算。

    纯函数：不修改 ``manifest`` / ``request`` / ``budget_state`` 三个输入，也
    不做任何重试、预算扩容或通知。判定顺序固定如下，命中即停：

    1. 无清单 -> ``missing_manifest``；
    2. 环境别名与清单不一致 -> ``environment_mismatch``；
    3. ``requested_at < valid_from`` -> ``not_yet_valid``；
       ``requested_at >= valid_until`` -> ``expired``；
    4. 资产别名不在清单范围内 -> ``asset_out_of_scope``；
    5. 操作不在清单范围内 -> ``operation_not_allowed``；
    6. 总预算已耗尽 -> ``total_budget_exhausted``；
    7. 单操作预算已耗尽 -> ``operation_budget_exhausted``；
    8. 全部通过 -> 原子地把总计数与单操作计数各 +1 后放行。

    命中第 6 / 7 条时两个预算都视为未消费（deny 不消费预算）；放行时两个计数
    在同一个新状态里同时递增，不会只递增其中一个。
    """
    if manifest is None:
        return AuthorizationDecision(
            allowed=False,
            deny_reason=AuthorizationDenyReason.MISSING_MANIFEST,
            manifest_id=None,
            budget_state=budget_state,
        )

    manifest_id = manifest.manifest_id
    if request.environment_alias != manifest.environment_alias:
        return _deny(
            AuthorizationDenyReason.ENVIRONMENT_MISMATCH, manifest_id, budget_state
        )
    if request.requested_at < manifest.valid_from:
        return _deny(AuthorizationDenyReason.NOT_YET_VALID, manifest_id, budget_state)
    if request.requested_at >= manifest.valid_until:
        return _deny(AuthorizationDenyReason.EXPIRED, manifest_id, budget_state)
    if request.asset_alias not in manifest.asset_scope_aliases:
        return _deny(AuthorizationDenyReason.ASSET_OUT_OF_SCOPE, manifest_id, budget_state)
    if request.operation not in manifest.allowed_operations:
        return _deny(AuthorizationDenyReason.OPERATION_NOT_ALLOWED, manifest_id, budget_state)
    if budget_state.total_calls_consumed >= manifest.max_total_calls:
        return _deny(AuthorizationDenyReason.TOTAL_BUDGET_EXHAUSTED, manifest_id, budget_state)
    if (
        budget_state.consumed_for(request.operation)
        >= manifest.max_calls_per_operation[request.operation]
    ):
        return _deny(
            AuthorizationDenyReason.OPERATION_BUDGET_EXHAUSTED, manifest_id, budget_state
        )

    return AuthorizationDecision(
        allowed=True,
        deny_reason=None,
        manifest_id=manifest_id,
        budget_state=_consume(budget_state, request.operation),
    )


def _deny(
    reason: AuthorizationDenyReason,
    manifest_id: str,
    budget_state: AuthorizationBudgetState,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=False,
        deny_reason=reason,
        manifest_id=manifest_id,
        budget_state=budget_state,
    )


def _consume(
    budget_state: AuthorizationBudgetState,
    operation: DeviceReadOperation,
) -> AuthorizationBudgetState:
    """原子消费一次预算：返回新状态，两个计数同时递增。"""
    updated = dict(budget_state.calls_by_operation)
    updated[operation] = updated.get(operation, 0) + 1
    return AuthorizationBudgetState(
        total_calls_consumed=budget_state.total_calls_consumed + 1,
        calls_by_operation=updated,
    )


class DeviceAuthorizationSession:
    """一次运行期共享的授权与预算载体。

    网关持有该对象，并在同一把锁内完成预检和预算状态替换。这样并发调用也不能
    复用同一份旧状态越过预算；调用方无法为每次调用临时传入空预算。
    """

    def __init__(
        self,
        manifest: AuthorizationManifest | None,
        *,
        environment_alias: str,
        initial_budget_state: AuthorizationBudgetState,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._manifest = manifest
        self._environment_alias = _validate_alias(environment_alias, "environment_alias")
        self._budget_state = initial_budget_state
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._request_sequence = 0

    @property
    def budget_state(self) -> AuthorizationBudgetState:
        with self._lock:
            return self._budget_state

    def authorize(
        self,
        *,
        asset_alias: str,
        operation: DeviceReadOperation,
    ) -> AuthorizationDecision:
        """原子预检一次调用，并持久保留返回的预算状态。"""
        with self._lock:
            self._request_sequence += 1
            decision = preflight_device_call(
                self._manifest,
                AuthorizationRequest(
                    request_id=f"gateway-{self._request_sequence}",
                    environment_alias=self._environment_alias,
                    asset_alias=asset_alias,
                    operation=operation,
                    requested_at=self._clock(),
                ),
                self._budget_state,
            )
            self._budget_state = decision.budget_state
            return decision


__all__ = [
    "READ_ONLY_OPERATIONS",
    "AuthorizationBudgetState",
    "AuthorizationDecision",
    "AuthorizationDenyReason",
    "AuthorizationManifest",
    "AuthorizationRequest",
    "DeviceReadOperation",
    "DeviceAuthorizationSession",
    "preflight_device_call",
]
