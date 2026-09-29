"""API 监听的启动门禁：正式入口始终要求双凭证，无认证 Demo 必须显式开启。"""

import importlib.util
from pathlib import Path

import pytest

from security_diagnosis_harness.config import RuntimeConfigurationError

RUN_API_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_api.py"


def _load_run_api():
    spec = importlib.util.spec_from_file_location("run_api_auth_probe", RUN_API_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clear_security_env(module, monkeypatch) -> None:
    for name in (
        module.API_HOST_ENV_VAR,
        module.API_TOKEN_ENV_VAR,
        module.API_REVIEWER_TOKEN_ENV_VAR,
        module.API_DEMO_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)


def test_external_listen_requires_token(monkeypatch):
    module = _load_run_api()
    _clear_security_env(module, monkeypatch)
    monkeypatch.setenv(module.API_HOST_ENV_VAR, "0.0.0.0")

    with pytest.raises(RuntimeConfigurationError, match="身份认证"):
        module.resolve_api_security()


def test_formal_entry_requires_credentials_even_on_loopback(monkeypatch):
    """正式入口即使只监听 localhost 也必须启用双凭证，不再静默降级。"""
    module = _load_run_api()
    _clear_security_env(module, monkeypatch)

    with pytest.raises(RuntimeConfigurationError, match="身份认证"):
        module.resolve_api_security()


def test_explicit_demo_on_loopback_returns_no_authenticator(monkeypatch):
    module = _load_run_api()
    _clear_security_env(module, monkeypatch)
    monkeypatch.setenv(module.API_DEMO_ENV_VAR, "true")

    host, authenticator = module.resolve_api_security()

    assert host == "127.0.0.1"
    assert authenticator is None


def test_demo_mode_is_rejected_for_non_loopback_listen(monkeypatch):
    module = _load_run_api()
    _clear_security_env(module, monkeypatch)
    monkeypatch.setenv(module.API_HOST_ENV_VAR, "0.0.0.0")
    monkeypatch.setenv(module.API_DEMO_ENV_VAR, "1")

    with pytest.raises(RuntimeConfigurationError, match="回环"):
        module.resolve_api_security()


def test_demo_mode_must_not_also_carry_credentials(monkeypatch):
    module = _load_run_api()
    _clear_security_env(module, monkeypatch)
    monkeypatch.setenv(module.API_DEMO_ENV_VAR, "true")
    monkeypatch.setenv(module.API_TOKEN_ENV_VAR, "operator-token")
    monkeypatch.setenv(module.API_REVIEWER_TOKEN_ENV_VAR, "reviewer-token")

    with pytest.raises(RuntimeConfigurationError, match="Demo"):
        module.resolve_api_security()


def test_external_listen_with_separated_tokens_builds_redacted_authenticator(monkeypatch):
    module = _load_run_api()
    monkeypatch.setenv(module.API_HOST_ENV_VAR, "0.0.0.0")
    monkeypatch.setenv(module.API_TOKEN_ENV_VAR, "external-secret")
    monkeypatch.setenv(module.API_ACTOR_ENV_VAR, "ops-api")
    monkeypatch.setenv(module.API_REVIEWER_TOKEN_ENV_VAR, "review-secret")
    monkeypatch.setenv(module.API_REVIEWER_ACTOR_ENV_VAR, "review-api")

    host, authenticator = module.resolve_api_security()

    assert host == "0.0.0.0"
    assert authenticator is not None
    assert "external-secret" not in repr(authenticator)
    assert "review-secret" not in repr(authenticator)


def test_partial_or_reused_credentials_are_rejected(monkeypatch):
    module = _load_run_api()
    monkeypatch.delenv(module.API_DEMO_ENV_VAR, raising=False)
    monkeypatch.setenv(module.API_TOKEN_ENV_VAR, "same-secret")
    monkeypatch.delenv(module.API_REVIEWER_TOKEN_ENV_VAR, raising=False)
    with pytest.raises(RuntimeConfigurationError, match="同时配置"):
        module.resolve_api_security()

    monkeypatch.setenv(module.API_REVIEWER_TOKEN_ENV_VAR, "same-secret")
    with pytest.raises(RuntimeConfigurationError, match="token"):
        module.resolve_api_security()
