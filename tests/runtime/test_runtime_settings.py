"""Phase 6B-1：RuntimeSettings 运行配置验收。

覆盖：默认正式模式、优先级（显式 > 环境变量 > 默认）、非法值受控失败、
URL 白名单、错误输出不泄漏凭证、不读 `.env`。
"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.config import (
    REPOSITORY_MODE_ENV_VAR,
    DatabaseEchoEnvVar,
    RepositoryMode,
    RuntimeConfigurationError,
    RuntimeSettings,
)

MODE_ENV = "SECURITY_DIAGNOSIS_REPOSITORY"
URL_ENV = "SECURITY_DIAGNOSIS_DB_URL"
ECHO_ENV = "SECURITY_DIAGNOSIS_DB_ECHO"
MIGRATE_ENV = "SECURITY_DIAGNOSIS_AUTO_MIGRATE"

ALL_ENV_VARS = (MODE_ENV, URL_ENV, ECHO_ENV, MIGRATE_ENV)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------- 默认值
def test_default_mode_is_sqlite():
    settings = RuntimeSettings()

    assert settings.repository_mode is RepositoryMode.SQLITE
    assert settings.database_url == "sqlite:///./data/security-diagnosis.db"
    assert settings.database_echo is False
    assert settings.auto_migrate is True


def test_repository_mode_env_var_name_matches_baseline():
    assert REPOSITORY_MODE_ENV_VAR == MODE_ENV
    assert MIGRATE_ENV == "SECURITY_DIAGNOSIS_AUTO_MIGRATE"


def test_database_echo_env_var_is_read(monkeypatch):
    monkeypatch.setenv(ECHO_ENV, "true")

    assert RuntimeSettings().database_echo is True


def test_explicit_memory_mode_is_supported():
    settings = RuntimeSettings(repository_mode="memory")

    assert settings.repository_mode is RepositoryMode.MEMORY


# ---------------------------------------------------------------- 优先级
def test_explicit_argument_overrides_environment(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "memory")

    settings = RuntimeSettings(repository_mode="sqlite")

    assert settings.repository_mode is RepositoryMode.SQLITE


def test_environment_overrides_default(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "memory")
    monkeypatch.setenv(URL_ENV, "sqlite:///./tmp/env.db")
    monkeypatch.setenv(ECHO_ENV, "true")
    monkeypatch.setenv(MIGRATE_ENV, "false")

    settings = RuntimeSettings()

    assert settings.repository_mode is RepositoryMode.MEMORY
    assert settings.database_url == "sqlite:///./tmp/env.db"
    assert settings.database_echo is True
    assert settings.auto_migrate is False


# ---------------------------------------------------------------- 非法值
def test_invalid_repository_mode_is_controlled_failure(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "postgres")

    with pytest.raises(RuntimeConfigurationError):
        RuntimeSettings()


def test_invalid_boolean_is_controlled_failure(monkeypatch):
    monkeypatch.setenv(ECHO_ENV, "maybe")

    with pytest.raises(RuntimeConfigurationError):
        RuntimeSettings()


def test_invalid_auto_migrate_boolean_is_controlled_failure(monkeypatch):
    monkeypatch.setenv(MIGRATE_ENV, "sure")

    with pytest.raises(RuntimeConfigurationError):
        RuntimeSettings()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pass@localhost:5432/db",
        "postgresql+psycopg://user:pass@localhost/db",
        "mysql+pymysql://user:pass@localhost/db",
        "mssql+pyodbc://user:pass@server/db",
        "oracle://user:pass@server/db",
    ],
)
def test_non_sqlite_urls_are_rejected(url: str):
    with pytest.raises(RuntimeConfigurationError):
        RuntimeSettings(database_url=url)


def test_sqlite_memory_url_is_allowed():
    settings = RuntimeSettings(database_url="sqlite:///:memory:")

    assert settings.database_url == "sqlite:///:memory:"


def test_sqlite_file_url_is_allowed():
    settings = RuntimeSettings(database_url="sqlite:///./data/runtime.db")

    assert settings.database_url == "sqlite:///./data/runtime.db"


# ---------------------------------------------------------------- 安全输出
def test_repr_and_str_do_not_leak_full_database_url():
    settings = RuntimeSettings(database_url="sqlite:///./data/secret-name.db")

    for text in (repr(settings), str(settings)):
        assert "sqlite:///./data/secret-name.db" not in text
        assert "secret-name" not in text


def test_error_message_does_not_leak_database_url():
    with pytest.raises(RuntimeConfigurationError) as excinfo:
        RuntimeSettings(database_url="postgresql://admin:super-secret@db.internal:5432/app")

    message = str(excinfo.value)
    assert "super-secret" not in message
    assert "db.internal" not in message


def test_safe_database_target_hides_path_but_keeps_scheme():
    settings = RuntimeSettings(database_url="sqlite:///./data/runtime.db")

    target = settings.safe_database_target()

    assert target.startswith("sqlite:///")
    assert "data" not in target
    assert "runtime.db" not in target


# ---------------------------------------------------------------- .env
def test_settings_do_not_read_dotenv(tmp_path, monkeypatch):
    import os

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"{MODE_ENV}=memory\n{URL_ENV}=sqlite:///./from-dotenv.db\n",
        encoding="utf-8",
    )
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    settings = RuntimeSettings()

    assert settings.repository_mode is RepositoryMode.SQLITE
    assert settings.database_url != "sqlite:///./from-dotenv.db"
    assert os.environ.get(MODE_ENV) is None


# ---------------------------------------------------------------- 纯度
def test_settings_hold_no_infrastructure_objects():
    settings = RuntimeSettings()

    for name in settings.__slots__:
        value = getattr(settings, name)
        assert not hasattr(value, "dispose")
        assert not hasattr(value, "bind")


def test_database_echo_env_var_constant_is_exposed():
    assert DatabaseEchoEnvVar == ECHO_ENV
