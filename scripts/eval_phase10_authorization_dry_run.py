"""Phase 10C 授权协议 dry-run；不执行设备调用或外部访问。"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.device_authorization import (  # noqa: E402
    AuthorizationBudgetState,
    AuthorizationManifest,
    AuthorizationRequest,
    DeviceReadOperation,
    preflight_device_call,
)


def evaluate() -> dict:
    now = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
    manifest = AuthorizationManifest(
        manifest_id="dry-run-manifest",
        environment_alias="pilot-lab",
        asset_scope_aliases=frozenset({"camera-pilot"}),
        credential_ref="vault-ref:camera-readonly",
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
        allowed_operations=frozenset({DeviceReadOperation.QUERY_STATUS}),
        max_total_calls=1,
        max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 1},
    )
    state = AuthorizationBudgetState()
    allowed = preflight_device_call(
        manifest,
        AuthorizationRequest(
            request_id="dry-run-allow",
            environment_alias="pilot-lab",
            asset_alias="camera-pilot",
            operation=DeviceReadOperation.QUERY_STATUS,
            requested_at=now,
        ),
        state,
    )
    exhausted = preflight_device_call(
        manifest,
        AuthorizationRequest(
            request_id="dry-run-budget-deny",
            environment_alias="pilot-lab",
            asset_alias="camera-pilot",
            operation=DeviceReadOperation.QUERY_STATUS,
            requested_at=now,
        ),
        allowed.budget_state,
    )
    missing = preflight_device_call(
        None,
        AuthorizationRequest(
            request_id="dry-run-missing-deny",
            environment_alias="pilot-lab",
            asset_alias="camera-pilot",
            operation=DeviceReadOperation.QUERY_STATUS,
            requested_at=now,
        ),
        state,
    )
    return {
        "report_kind": "authorization_dry_run",
        "real_device_called": False,
        "external_network_accessed": False,
        "device_write_attempted": False,
        "automatic_retry": False,
        "allow_path": allowed.allowed,
        "budget_deny_reason": exhausted.deny_reason.value,
        "missing_manifest_reason": missing.deny_reason.value,
        "deny_preserved_budget": exhausted.budget_state == allowed.budget_state,
        "passed": (
            allowed.allowed
            and not exhausted.allowed
            and not missing.allowed
            and exhausted.budget_state == allowed.budget_state
        ),
    }


def main() -> int:
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
