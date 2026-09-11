"""正式运行配置（Phase 6B-1）。

设计约束：

- 优先级：**显式参数 > 环境变量 > 安全默认值**；
- 只支持本地 SQLite（文件或 `:memory:`），拒绝网络数据库；
- 不自动读取 `.env`；
- 不在 repr / str / 异常信息中输出完整 `database_url`；
- 配置对象**不持有** Engine / Session 等基础设施对象。
"""

from __future__ import annotations

import os
from enum import StrEnum

REPOSITORY_MODE_ENV_VAR = "SECURITY_DIAGNOSIS_REPOSITORY"
DATABASE_URL_ENV_VAR = "SECURITY_DIAGNOSIS_DB_URL"
DatabaseEchoEnvVar = "SECURITY_DIAGNOSIS_DB_ECHO"
AUTO_MIGRATE_ENV_VAR = "SECURITY_DIAGNOSIS_AUTO_MIGRATE"

DEFAULT_DATABASE_URL = "sqlite:///./data/security-diagnosis.db"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# 本阶段只允许本地 SQLite；其余方言（含网络数据库）一律拒绝。
_ALLOWED_URL_PREFIXES = ("sqlite:///", "sqlite+pysqlite:///")


class RuntimeConfigurationError(Exception):
    """正式运行配置非法（受控失败，不向外抛裸 ValueError）。"""


class RepositoryMode(StrEnum):
    """仓储装配模式。"""

    MEMORY = "memory"
    SQLITE = "sqlite"


def _parse_bool(raw: str, *, name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RuntimeConfigurationError(f"环境变量 {name} 需要布尔值（true/false），当前值非法")


def _resolve_bool(
    explicit: bool | None,
    *,
    env_var: str,
    default: bool,
) -> bool:
    if explicit is not None:
        return explicit
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    return _parse_bool(raw, name=env_var)


def _resolve_str(explicit: str | None, *, env_var: str, default: str) -> str:
    if explicit:
        return explicit
    raw = os.environ.get(env_var, "").strip()
    return raw or default


def _validate_database_url(database_url: str) -> str:
    if not database_url.startswith(_ALLOWED_URL_PREFIXES):
        # 刻意不回显 URL 本身，避免把凭证写进异常信息。
        raise RuntimeConfigurationError(
            "本阶段只允许本地 SQLite 数据库（sqlite:/// 或 sqlite+pysqlite:///）"
        )
    return database_url


def _safe_target(database_url: str) -> str:
    """给出可安全记录的数据库目标描述（不含路径与凭证）。"""
    scheme = database_url.split("://", 1)[0]
    if database_url.endswith(":memory:"):
        return f"{scheme}:///<memory>"
    return f"{scheme}:///<local-file>"


_UNSET: object = object()


class RuntimeSettings:
    """正式运行配置。

    Args:
        repository_mode: `sqlite`（正式默认）或 `memory`；
            不传时读取 `SECURITY_DIAGNOSIS_REPOSITORY`，再回落默认。
        database_url: 仅允许本地 SQLite URL；不传时读取环境变量。
        database_echo: 是否输出 SQL echo；不传时读取环境变量。
        auto_migrate: 构建运行时是否自动执行 Alembic upgrade head；不传时读取环境变量。

    优先级：**显式参数 > 环境变量 > 安全默认值**。
    """

    __slots__ = ("_safe_target", "auto_migrate", "database_echo", "database_url", "repository_mode")

    def __init__(
        self,
        *,
        repository_mode: str | RepositoryMode | None = None,
        database_url: str | None = None,
        database_echo: bool | None = None,
        auto_migrate: bool | None = None,
    ) -> None:
        resolved_mode = _coerce_mode(repository_mode)
        resolved_url = _validate_database_url(
            _resolve_str(database_url, env_var=DATABASE_URL_ENV_VAR, default=DEFAULT_DATABASE_URL)
        )
        self.repository_mode: RepositoryMode = resolved_mode
        self.database_url: str = resolved_url
        self.database_echo: bool = _resolve_bool(
            database_echo, env_var=DatabaseEchoEnvVar, default=False
        )
        self.auto_migrate: bool = _resolve_bool(
            auto_migrate, env_var=AUTO_MIGRATE_ENV_VAR, default=True
        )
        self._safe_target: str = _safe_target(resolved_url)

    # ------------------------------------------------------------------ 安全输出
    def safe_database_target(self) -> str:
        """返回可安全记录的数据库目标（不含路径与凭证）。"""
        return self._safe_target

    def __repr__(self) -> str:
        return (
            f"RuntimeSettings(repository_mode={self.repository_mode.value!r}, "
            f"database={self._safe_target!r}, "
            f"database_echo={self.database_echo!r}, "
            f"auto_migrate={self.auto_migrate!r})"
        )

    __str__ = __repr__


def _coerce_mode(value: object) -> RepositoryMode:
    """解析仓储模式；显式值优先，其次环境变量，最后默认 sqlite。"""
    if isinstance(value, RepositoryMode):
        return value
    if value is None:
        raw = os.environ.get(REPOSITORY_MODE_ENV_VAR, "").strip()
        raw = raw or RepositoryMode.SQLITE.value
    else:
        raw = str(value).strip()
    try:
        return RepositoryMode(raw.lower())
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in RepositoryMode)
        # 不回显用户输入，避免潜在凭证泄漏。
        raise RuntimeConfigurationError(f"repository_mode 非法，只能是：{allowed}") from exc


def build_runtime_settings(
    *,
    repository_mode: str | RepositoryMode | None = None,
    database_url: str | None = None,
    database_echo: bool | None = None,
    auto_migrate: bool | None = None,
) -> RuntimeSettings:
    """按 显式参数 > 环境变量 > 默认值 组装运行配置。"""
    return RuntimeSettings(
        repository_mode=repository_mode,
        database_url=database_url,
        database_echo=database_echo,
        auto_migrate=auto_migrate,
    )


__all__ = [
    "AUTO_MIGRATE_ENV_VAR",
    "DATABASE_URL_ENV_VAR",
    "DEFAULT_DATABASE_URL",
    "DatabaseEchoEnvVar",
    "REPOSITORY_MODE_ENV_VAR",
    "RepositoryMode",
    "RuntimeConfigurationError",
    "RuntimeSettings",
    "build_runtime_settings",
]
