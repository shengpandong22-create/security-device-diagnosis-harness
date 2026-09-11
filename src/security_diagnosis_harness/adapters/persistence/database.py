"""SQLite 持久化基座：Engine / Session 工厂与 URL 解析。

Phase 6A 只负责给出可复用的同步 SQLite 装配：

- URL 由调用方传入，或读取 `SECURITY_DIAGNOSIS_DB_URL` 环境变量；
- 默认值是安全的本地示例 `sqlite:///./data/security-diagnosis.db`；
- 自动测试必须显式传入临时目录 URL，禁止污染仓库。

本模块不读 `.env`，也不做任何设备 / 模型访问。
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./data/security-diagnosis.db"
DATABASE_URL_ENV_VAR = "SECURITY_DIAGNOSIS_DB_URL"


def resolve_database_url(database_url: str | None = None) -> str:
    """按 显式参数 > 环境变量 > 本地默认 的优先级解析数据库 URL。"""
    if database_url:
        return database_url
    from_env = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if from_env:
        return from_env
    return DEFAULT_DATABASE_URL


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    """构造同步 SQLAlchemy Engine。

    SQLite 需要显式放行同一个线程内的连接复用，否则 FastAPI / 测试的
    多线程场景会抛 `check_same_thread` 错误。
    """
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=echo, future=True, connect_args=connect_args)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """构造 Session 工厂。"""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def build_database(
    database_url: str | None = None,
    *,
    echo: bool = False,
) -> tuple[Engine, sessionmaker[Session]]:
    """一次性构造 (Engine, Session 工厂)，并按需预建 SQLite 目录。"""
    resolved = resolve_database_url(database_url)
    ensure_sqlite_directory(resolved)
    engine = build_engine(resolved, echo=echo)
    return engine, build_session_factory(engine)


def ensure_sqlite_directory(database_url: str) -> None:
    """为文件型 SQLite URL 预建父目录，避免首次连接失败。

    仅处理 `sqlite:///` 形式（相对路径）与 `sqlite:////`（绝对路径），
    内存库与其它方言直接跳过。
    """
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw_path = database_url[len(prefix) :]
    if not raw_path or raw_path == ":memory:":
        return
    Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


__all__ = [
    "DATABASE_URL_ENV_VAR",
    "DEFAULT_DATABASE_URL",
    "build_database",
    "build_engine",
    "build_session_factory",
    "ensure_sqlite_directory",
    "resolve_database_url",
]
