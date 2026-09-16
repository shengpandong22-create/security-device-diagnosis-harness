"""正式本地 API 运行入口（Phase 6B-1）。

与 `create_app()` 的区别：

- `create_app()` 默认是**安全的内存/测试装配**，导入无副作用；
- 本脚本才是**正式本地 SQLite 运行入口**：读取 RuntimeSettings、
  构建 RuntimeContainer（含按配置执行 Alembic upgrade head）、
  启动 uvicorn，并在退出时 dispose Engine。

用法：

    uv run python scripts/run_api.py

环境变量：

    SECURITY_DIAGNOSIS_REPOSITORY=sqlite
    SECURITY_DIAGNOSIS_DB_URL=sqlite:///./data/security-diagnosis.db
    SECURITY_DIAGNOSIS_DB_ECHO=false
    SECURITY_DIAGNOSIS_AUTO_MIGRATE=true

非回环监听（例如 0.0.0.0）必须额外配置：

    SECURITY_DIAGNOSIS_API_HOST=0.0.0.0
    SECURITY_DIAGNOSIS_API_TOKEN=<由部署环境注入，不写入仓库>
    SECURITY_DIAGNOSIS_API_ACTOR=ops-api
"""

from __future__ import annotations

import os
import sys
from ipaddress import ip_address
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.api.app import create_app  # noqa: E402
from security_diagnosis_harness.api.auth import BearerAuthenticator  # noqa: E402
from security_diagnosis_harness.config import (  # noqa: E402
    RuntimeConfigurationError,
    build_runtime_settings,
)
from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
API_HOST_ENV_VAR = "SECURITY_DIAGNOSIS_API_HOST"
API_TOKEN_ENV_VAR = "SECURITY_DIAGNOSIS_API_TOKEN"
API_ACTOR_ENV_VAR = "SECURITY_DIAGNOSIS_API_ACTOR"


def resolve_api_security() -> tuple[str, BearerAuthenticator | None]:
    """解析监听地址；非回环监听必须启用 Bearer 身份授权。"""
    host = os.environ.get(API_HOST_ENV_VAR, DEFAULT_HOST).strip() or DEFAULT_HOST
    token = os.environ.get(API_TOKEN_ENV_VAR, "")
    actor = os.environ.get(API_ACTOR_ENV_VAR, "api-operator").strip()
    try:
        is_loopback = ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback and not token:
        raise RuntimeConfigurationError("非回环 API 监听必须配置身份认证")
    authenticator = (
        BearerAuthenticator(
            token,
            actor=actor,
            roles=frozenset({"operator", "reviewer"}),
        )
        if token
        else None
    )
    return host, authenticator


def build_app():
    """构建 (app, runtime)；调用方负责在退出时 `runtime.close()`。

    若 `create_app` 失败，必须在这里就 release 已创建的 Runtime，
    否则调用方拿不到对象，Engine 会泄漏。
    """
    settings = build_runtime_settings()
    _, authenticator = resolve_api_security()
    runtime = build_runtime_container(settings)
    try:
        app = create_app(
            runtime.service,
            repository_mode=settings.repository_mode.value,
            database_ready=runtime.database_ready,
            authenticator=authenticator,
        )
    except Exception:
        runtime.close()
        raise
    return app, runtime


def main() -> int:
    import uvicorn

    # 迁移失败会在这里抛出，不返回可用的 service。
    host, _ = resolve_api_security()
    app, runtime = build_app()
    try:
        uvicorn.run(app, host=host, port=DEFAULT_PORT, reload=False)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
