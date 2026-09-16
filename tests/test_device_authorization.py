"""Phase 10B-1：授权清单与调用前预算预检验收。

重点覆盖：不透明凭证引用、只读 allowlist、默认拒绝的全部原因码、半开时间窗
``valid_from <= now < valid_until``、deny 不消费预算、放行时总预算与单操作预算的
原子递增，以及预检函数的纯函数性与无副作用。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.device_authorization import (
    READ_ONLY_OPERATIONS,
    AuthorizationBudgetState,
    AuthorizationDecision,
    AuthorizationDenyReason,
    AuthorizationManifest,
    AuthorizationRequest,
    AuthorizationSessionClosedError,
    DeviceAuthorizationSession,
    DeviceReadOperation,
    preflight_device_call,
)

VALID_FROM = datetime(2026, 1, 1, tzinfo=UTC)
VALID_UNTIL = datetime(2026, 2, 1, tzinfo=UTC)
NOW = datetime(2026, 1, 15, tzinfo=UTC)

READ_PREFIXES = ("query_", "check_", "search_", "read_")


def _manifest(**overrides) -> AuthorizationManifest:
    base: dict = {
        "manifest_id": "manifest-1",
        "environment_alias": "env-lab",
        "asset_scope_aliases": frozenset({"camera-a"}),
        "credential_ref": "vault-ref:camera-readonly",
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
        "allowed_operations": frozenset({DeviceReadOperation.QUERY_STATUS}),
        "max_total_calls": 3,
        "max_calls_per_operation": {DeviceReadOperation.QUERY_STATUS: 2},
    }
    base.update(overrides)
    return AuthorizationManifest(**base)


def _request(**overrides) -> AuthorizationRequest:
    base: dict = {
        "request_id": "req-1",
        "environment_alias": "env-lab",
        "asset_alias": "camera-a",
        "operation": DeviceReadOperation.QUERY_STATUS,
        "requested_at": NOW,
    }
    base.update(overrides)
    return AuthorizationRequest(**base)


# --------------------------------------------------------------- 只读 allowlist


def test_read_only_operations_are_declared_reads() -> None:
    operations = {operation.value for operation in READ_ONLY_OPERATIONS}

    assert operations == {operation.value for operation in DeviceReadOperation}
    assert all(name.startswith(READ_PREFIXES) for name in operations)
    for forbidden in ("write", "set", "update", "delete", "reboot", "reset", "create"):
        assert not any(forbidden in name for name in operations)


def test_read_only_operations_match_device_gateway_read_methods() -> None:
    assert DeviceReadOperation.QUERY_STATUS.value == "query_status"
    assert DeviceReadOperation.READ_CONFIG_SNAPSHOT.value == "read_config_snapshot"
    assert len(READ_ONLY_OPERATIONS) == 19


@pytest.mark.parametrize(
    "operation",
    ["reboot_device", "write_config", "read_everything", "set_time", "query_status "],
)
def test_request_rejects_operation_outside_allowlist(operation: str) -> None:
    with pytest.raises(ValidationError):
        _request(operation=operation)


def test_request_coerces_allowlisted_string_operation() -> None:
    request = _request(operation="query_status")

    assert request.operation is DeviceReadOperation.QUERY_STATUS


# ------------------------------------------------- 不透明 credential_ref 校验


@pytest.mark.parametrize(
    "credential_ref",
    [
        "vault-ref:camera-readonly",
        "vault-ref:camera_readonly",
        "vault-ref:c1",
        "vault:camera-a",
    ],
)
def test_credential_reference_accepts_opaque_references(credential_ref: str) -> None:
    assert _manifest(credential_ref=credential_ref).credential_ref == credential_ref


@pytest.mark.parametrize(
    "credential_ref",
    [
        "https://vault.internal/v1/camera",
        "vault-ref://camera",
        "vault-ref:user@host",
        "vault-ref:user:pass@host",
        "vault-ref:10.0.0.1",
        "10.0.0.1",
        "vault-ref:camera-readonly-secret",
        "vault-ref:access-token-abc",
        "vault-ref:camera-password",
        "vault-ref:my-api_key",
        "vault-ref:AKIAIOSFODNN7EXAMPLE",
        "camera-readonly",
        "",
        "vault-ref:camera readonly",
        "vault-ref:camera?token=x",
        "Bearer abcdefghijklmnop",
    ],
)
def test_credential_reference_rejects_secret_like_values(credential_ref: str) -> None:
    with pytest.raises(ValidationError):
        _manifest(credential_ref=credential_ref)


def test_invalid_credential_reference_does_not_echo_value() -> None:
    leaked = "vault-ref:camera-readonly-secret-sauce"

    with pytest.raises(ValidationError) as excinfo:
        _manifest(credential_ref=leaked)

    assert leaked not in str(excinfo.value)


# ------------------------------------------------------------------- 别名校验


@pytest.mark.parametrize("alias", ["env-lab", "site.a", "env1"])
def test_aliases_accept_controlled_names(alias: str) -> None:
    assert _manifest(environment_alias=alias).environment_alias == alias
    assert _request(asset_alias=alias).asset_alias == alias


@pytest.mark.parametrize(
    "alias",
    [
        "https://env.example",
        "env@host",
        "10.1.2.3",
        "env-lab-secret",
        "env-token",
        "ENV-LAB",
        "env lab",
        "",
    ],
)
def test_aliases_reject_endpoints_ips_and_credentials(alias: str) -> None:
    with pytest.raises(ValidationError):
        _manifest(environment_alias=alias)
    with pytest.raises(ValidationError):
        _request(asset_alias=alias)
    with pytest.raises(ValidationError):
        _manifest(asset_scope_aliases=frozenset({alias}))


# --------------------------------------------------------------- 清单一致性


def test_manifest_requires_aware_time_window() -> None:
    naive = datetime(2026, 1, 1)

    with pytest.raises(ValidationError):
        _manifest(valid_from=naive)
    with pytest.raises(ValidationError):
        _manifest(valid_until=naive)


def test_manifest_rejects_inverted_time_window() -> None:
    with pytest.raises(ValidationError):
        _manifest(valid_from=VALID_UNTIL, valid_until=VALID_FROM)
    with pytest.raises(ValidationError):
        _manifest(valid_from=VALID_FROM, valid_until=VALID_FROM)


def test_manifest_rejects_non_read_only_operation() -> None:
    with pytest.raises(ValidationError):
        _manifest(allowed_operations=frozenset({"write_config"}))


def test_manifest_requires_budget_for_every_allowed_operation() -> None:
    with pytest.raises(ValidationError):
        _manifest(
            allowed_operations=frozenset({DeviceReadOperation.QUERY_DOOR}),
            max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 1},
        )


def test_manifest_rejects_negative_budgets() -> None:
    with pytest.raises(ValidationError):
        _manifest(max_total_calls=-1)
    with pytest.raises(ValidationError):
        _manifest(max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: -1})


def test_manifest_is_frozen_and_forbids_extra_fields() -> None:
    manifest = _manifest()

    with pytest.raises(ValidationError):
        manifest.manifest_id = "manifest-2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AuthorizationManifest(**{**_manifest().model_dump(), "endpoint": "https://x"})


def test_request_requires_aware_time() -> None:
    with pytest.raises(ValidationError):
        _request(requested_at=datetime(2026, 1, 15))


# ---------------------------------------------------------------- 预算状态


def test_budget_state_default_is_empty() -> None:
    state = AuthorizationBudgetState()

    assert state.total_calls_consumed == 0
    assert state.consumed_for(DeviceReadOperation.QUERY_STATUS) == 0


def test_budget_state_copies_mutable_mapping() -> None:
    source = {DeviceReadOperation.QUERY_STATUS: 2}

    state = AuthorizationBudgetState.from_consumed(source)
    source[DeviceReadOperation.QUERY_STATUS] = 99

    assert state.total_calls_consumed == 2
    assert state.consumed_for(DeviceReadOperation.QUERY_STATUS) == 2


def test_budget_state_is_frozen() -> None:
    state = AuthorizationBudgetState()

    with pytest.raises(ValidationError):
        state.total_calls_consumed = 1  # type: ignore[misc]


def test_nested_budget_mappings_are_immutable() -> None:
    manifest = _manifest()
    state = AuthorizationBudgetState.from_consumed(
        {DeviceReadOperation.QUERY_STATUS: 1}
    )

    with pytest.raises(TypeError):
        manifest.max_calls_per_operation[DeviceReadOperation.QUERY_STATUS] = 99
    with pytest.raises(TypeError):
        state.calls_by_operation[DeviceReadOperation.QUERY_STATUS] = 99


def test_budget_state_rejects_inconsistent_total() -> None:
    with pytest.raises(ValidationError):
        AuthorizationBudgetState(
            total_calls_consumed=5,
            calls_by_operation={DeviceReadOperation.QUERY_STATUS: 1},
        )


def test_budget_state_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        AuthorizationBudgetState(calls_by_operation={DeviceReadOperation.QUERY_STATUS: -1})


# ------------------------------------------------------------------ 放行路径


def test_preflight_allows_within_window_and_scope() -> None:
    decision = preflight_device_call(_manifest(), _request(), AuthorizationBudgetState())

    assert decision.allowed is True
    assert decision.deny_reason is None
    assert decision.manifest_id == "manifest-1"
    assert decision.budget_state.total_calls_consumed == 1
    assert decision.budget_state.consumed_for(DeviceReadOperation.QUERY_STATUS) == 1


def test_preflight_allows_at_valid_from_boundary() -> None:
    decision = preflight_device_call(
        _manifest(), _request(requested_at=VALID_FROM), AuthorizationBudgetState()
    )

    assert decision.allowed is True


def test_preflight_does_not_mutate_inputs() -> None:
    manifest = _manifest()
    request = _request()
    state = AuthorizationBudgetState()

    decision = preflight_device_call(manifest, request, state)

    assert manifest.allowed_operations == frozenset({DeviceReadOperation.QUERY_STATUS})
    assert request.operation is DeviceReadOperation.QUERY_STATUS
    assert state.total_calls_consumed == 0
    assert state.calls_by_operation == {}
    assert decision.budget_state is not state


def test_preflight_consumes_exactly_once_and_atomically() -> None:
    manifest = _manifest(
        allowed_operations=frozenset(
            {DeviceReadOperation.QUERY_STATUS, DeviceReadOperation.QUERY_DOOR}
        ),
        max_calls_per_operation={
            DeviceReadOperation.QUERY_STATUS: 2,
            DeviceReadOperation.QUERY_DOOR: 2,
        },
    )
    state = AuthorizationBudgetState.from_consumed({DeviceReadOperation.QUERY_STATUS: 1})

    decision = preflight_device_call(manifest, _request(), state)

    assert decision.allowed is True
    assert decision.budget_state.total_calls_consumed == 2
    assert decision.budget_state.consumed_for(DeviceReadOperation.QUERY_STATUS) == 2
    assert decision.budget_state.consumed_for(DeviceReadOperation.QUERY_DOOR) == 0


# ------------------------------------------------------------------ 拒绝路径


def test_preflight_denies_missing_manifest() -> None:
    state = AuthorizationBudgetState()

    decision = preflight_device_call(None, _request(), state)

    assert decision.allowed is False
    assert decision.deny_reason is AuthorizationDenyReason.MISSING_MANIFEST
    assert decision.manifest_id is None
    assert decision.budget_state is state


def test_preflight_denies_environment_mismatch() -> None:
    decision = preflight_device_call(
        _manifest(), _request(environment_alias="env-other"), AuthorizationBudgetState()
    )

    assert decision.deny_reason is AuthorizationDenyReason.ENVIRONMENT_MISMATCH


def test_preflight_denies_not_yet_valid() -> None:
    decision = preflight_device_call(
        _manifest(),
        _request(requested_at=VALID_FROM - timedelta(microseconds=1)),
        AuthorizationBudgetState(),
    )

    assert decision.deny_reason is AuthorizationDenyReason.NOT_YET_VALID


def test_preflight_denies_at_valid_until_boundary() -> None:
    decision = preflight_device_call(
        _manifest(), _request(requested_at=VALID_UNTIL), AuthorizationBudgetState()
    )

    assert decision.deny_reason is AuthorizationDenyReason.EXPIRED

    later = preflight_device_call(
        _manifest(),
        _request(requested_at=VALID_UNTIL + timedelta(seconds=1)),
        AuthorizationBudgetState(),
    )
    assert later.deny_reason is AuthorizationDenyReason.EXPIRED


def test_preflight_denies_asset_out_of_scope() -> None:
    decision = preflight_device_call(
        _manifest(), _request(asset_alias="camera-b"), AuthorizationBudgetState()
    )

    assert decision.deny_reason is AuthorizationDenyReason.ASSET_OUT_OF_SCOPE


def test_preflight_denies_empty_manifest_scope() -> None:
    decision = preflight_device_call(
        _manifest(asset_scope_aliases=frozenset()), _request(), AuthorizationBudgetState()
    )

    assert decision.deny_reason is AuthorizationDenyReason.ASSET_OUT_OF_SCOPE


def test_preflight_denies_operation_not_allowed() -> None:
    manifest = _manifest(
        allowed_operations=frozenset({DeviceReadOperation.QUERY_DOOR}),
        max_calls_per_operation={DeviceReadOperation.QUERY_DOOR: 2},
    )

    decision = preflight_device_call(manifest, _request(), AuthorizationBudgetState())

    assert decision.deny_reason is AuthorizationDenyReason.OPERATION_NOT_ALLOWED


def test_preflight_denies_empty_allowlist() -> None:
    decision = preflight_device_call(
        _manifest(allowed_operations=frozenset(), max_calls_per_operation={}),
        _request(),
        AuthorizationBudgetState(),
    )

    assert decision.deny_reason is AuthorizationDenyReason.OPERATION_NOT_ALLOWED


def test_preflight_denies_when_total_budget_exhausted() -> None:
    manifest = _manifest(max_total_calls=2)
    state = AuthorizationBudgetState.from_consumed({DeviceReadOperation.QUERY_STATUS: 2})

    decision = preflight_device_call(manifest, _request(), state)

    assert decision.deny_reason is AuthorizationDenyReason.TOTAL_BUDGET_EXHAUSTED
    assert decision.budget_state is state
    assert decision.budget_state.total_calls_consumed == 2


def test_preflight_denies_when_operation_budget_exhausted() -> None:
    manifest = _manifest(max_total_calls=5)
    state = AuthorizationBudgetState.from_consumed({DeviceReadOperation.QUERY_STATUS: 2})

    decision = preflight_device_call(manifest, _request(), state)

    assert decision.deny_reason is AuthorizationDenyReason.OPERATION_BUDGET_EXHAUSTED
    assert decision.budget_state is state


def test_total_budget_wins_when_both_budgets_exhausted() -> None:
    manifest = _manifest(max_total_calls=2)
    state = AuthorizationBudgetState.from_consumed({DeviceReadOperation.QUERY_STATUS: 2})

    decision = preflight_device_call(manifest, _request(), state)

    assert decision.deny_reason is AuthorizationDenyReason.TOTAL_BUDGET_EXHAUSTED


def test_preflight_deny_never_consumes_budget() -> None:
    manifest = _manifest()
    state = AuthorizationBudgetState.from_consumed({DeviceReadOperation.QUERY_STATUS: 2})

    for _ in range(3):
        decision = preflight_device_call(manifest, _request(), state)
        assert decision.allowed is False
        assert decision.budget_state is state
        assert state.total_calls_consumed == 2


def test_time_gate_priority_beats_scope_and_operation() -> None:
    manifest = _manifest(
        allowed_operations=frozenset(),
        max_calls_per_operation={},
    )

    expired = preflight_device_call(
        manifest,
        _request(asset_alias="camera-z", requested_at=VALID_UNTIL),
        AuthorizationBudgetState(),
    )

    assert expired.deny_reason is AuthorizationDenyReason.EXPIRED


def test_preflight_is_deterministic_on_repeated_allow() -> None:
    manifest = _manifest(
        max_total_calls=10,
        max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 10},
    )
    state = AuthorizationBudgetState()

    first = preflight_device_call(manifest, _request(), state)
    second = preflight_device_call(manifest, _request(), first.budget_state)

    assert second.allowed is True
    assert second.budget_state.total_calls_consumed == 2
    assert state.total_calls_consumed == 0


# ------------------------------------------------------------ 决策类型不变式


def test_decision_rejects_allow_with_deny_reason() -> None:
    with pytest.raises(ValidationError):
        AuthorizationDecision(
            allowed=True,
            deny_reason=AuthorizationDenyReason.EXPIRED,
            manifest_id="manifest-1",
            budget_state=AuthorizationBudgetState(),
        )


def test_decision_rejects_deny_without_reason() -> None:
    with pytest.raises(ValidationError):
        AuthorizationDecision(allowed=False, budget_state=AuthorizationBudgetState())


def test_decision_rejects_allow_without_manifest() -> None:
    with pytest.raises(ValidationError):
        AuthorizationDecision(allowed=True, budget_state=AuthorizationBudgetState())


def test_decision_forbids_free_text_fields() -> None:
    with pytest.raises(ValidationError):
        AuthorizationDecision(
            allowed=False,
            deny_reason=AuthorizationDenyReason.EXPIRED,
            budget_state=AuthorizationBudgetState(),
            detail="raw exception text",
        )


# ------------------------------------------------------------------- 无副作用


def test_module_does_not_bind_environment_or_network_dependencies() -> None:
    module = importlib.import_module("security_diagnosis_harness.device_authorization")

    for forbidden in ("os", "socket", "httpx", "requests", "subprocess", "pathlib"):
        assert not hasattr(module, forbidden), forbidden


def test_importing_module_has_no_side_effects(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import security_diagnosis_harness.device_authorization",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.rglob("*")) == []


# ------------------------------------------------------------ 授权会话生命周期


def _session(**overrides) -> DeviceAuthorizationSession:
    base: dict = {
        "manifest": _manifest(),
        "environment_alias": "env-lab",
        "initial_budget_state": AuthorizationBudgetState(),
        # 固定时钟在授权时间窗内（NOW），不依赖真实当前时间。
        "clock": lambda: NOW,
    }
    base.update(overrides)
    return DeviceAuthorizationSession(**base)


def test_session_consumes_budget_atomically_across_calls() -> None:
    session = _session(
        manifest=_manifest(
            max_total_calls=2,
            max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 2},
        )
    )

    first = session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    )
    second = session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    )
    third = session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    )

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.deny_reason is AuthorizationDenyReason.TOTAL_BUDGET_EXHAUSTED
    assert session.budget_state.total_calls_consumed == 2


def test_session_budget_exhaustion_never_auto_resets() -> None:
    session = _session(
        manifest=_manifest(
            max_total_calls=1,
            max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 1},
        )
    )

    assert session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    ).allowed is True
    for _ in range(3):
        denied = session.authorize(
            asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
        )
        assert denied.allowed is False
        assert denied.deny_reason is AuthorizationDenyReason.TOTAL_BUDGET_EXHAUSTED
    # 耗尽后既不静默重置也不自动扩容。
    assert session.budget_state.total_calls_consumed == 1


def test_session_rotate_starts_new_batch_and_resets_budget() -> None:
    session = _session(
        manifest=_manifest(
            max_total_calls=1,
            max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 1},
        )
    )
    assert session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    ).allowed is True
    assert session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    ).allowed is False

    session.rotate(
        _manifest(
            max_total_calls=1,
            max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 1},
        )
    )

    assert session.batch_index == 1
    assert session.budget_state.total_calls_consumed == 0
    assert session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    ).allowed is True


def test_session_close_is_terminal_and_idempotent() -> None:
    session = _session()

    session.close()
    session.close()

    decision = session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    )
    assert decision.allowed is False
    assert decision.deny_reason is AuthorizationDenyReason.SESSION_CLOSED
    assert session.closed is True


def test_session_rotate_after_close_is_rejected() -> None:
    session = _session()
    session.close()

    with pytest.raises(AuthorizationSessionClosedError):
        session.rotate(_manifest())


def test_session_expiry_denies_at_valid_until_boundary() -> None:
    session = _session(clock=lambda: VALID_UNTIL)

    decision = session.authorize(
        asset_alias="camera-a", operation=DeviceReadOperation.QUERY_STATUS
    )

    assert decision.allowed is False
    assert decision.deny_reason is AuthorizationDenyReason.EXPIRED


def test_session_maps_invalid_asset_alias_to_stable_deny() -> None:
    session = _session()

    decision = session.authorize(
        asset_alias="Bad-Alias!", operation=DeviceReadOperation.QUERY_STATUS
    )

    assert decision.allowed is False
    assert decision.deny_reason is AuthorizationDenyReason.INVALID_ASSET_ALIAS
    # 非法 alias 不消费预算，也不抛出原始 Pydantic 错误。
    assert session.budget_state.total_calls_consumed == 0


def test_session_accepts_boundary_valid_asset_alias() -> None:
    alias = "a" * 64
    session = _session(manifest=_manifest(asset_scope_aliases=frozenset({alias})))

    decision = session.authorize(
        asset_alias=alias, operation=DeviceReadOperation.QUERY_STATUS
    )

    assert decision.allowed is True
