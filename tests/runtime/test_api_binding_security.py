"""非回环 API 监听的启动门禁。"""

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


def test_external_listen_requires_token(monkeypatch):
    module = _load_run_api()
    monkeypatch.setenv(module.API_HOST_ENV_VAR, "0.0.0.0")
    monkeypatch.delenv(module.API_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(module.API_REVIEWER_TOKEN_ENV_VAR, raising=False)

    with pytest.raises(RuntimeConfigurationError, match="身份认证"):
        module.resolve_api_security()


def test_loopback_keeps_safe_local_default(monkeypatch):
    module = _load_run_api()
    monkeypatch.delenv(module.API_HOST_ENV_VAR, raising=False)
    monkeypatch.delenv(module.API_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(module.API_REVIEWER_TOKEN_ENV_VAR, raising=False)

    host, authenticator = module.resolve_api_security()

    assert host == "127.0.0.1"
    assert authenticator is None


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
    monkeypatch.setenv(module.API_TOKEN_ENV_VAR, "same-secret")
    monkeypatch.delenv(module.API_REVIEWER_TOKEN_ENV_VAR, raising=False)
    with pytest.raises(RuntimeConfigurationError, match="同时配置"):
        module.resolve_api_security()

    monkeypatch.setenv(module.API_REVIEWER_TOKEN_ENV_VAR, "same-secret")
    with pytest.raises(RuntimeConfigurationError, match="token"):
        module.resolve_api_security()
