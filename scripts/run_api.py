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

正式入口（`scripts/run_api.py`）**始终**要求分离的 operator/reviewer 双凭证，
即使只监听回环地址：

    SECURITY_DIAGNOSIS_API_TOKEN=<由部署环境注入，不写入仓库>
    SECURITY_DIAGNOSIS_API_ACTOR=ops-api
    SECURITY_DIAGNOSIS_API_REVIEWER_TOKEN=<由部署环境注入，不写入仓库>
    SECURITY_DIAGNOSIS_API_REVIEWER_ACTOR=review-api

确需**无认证 Demo**（仅限回环监听）时必须显式开启：

    SECURITY_DIAGNOSIS_API_HOST=127.0.0.1
    SECURITY_DIAGNOSIS_API_DEMO=true
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
API_REVIEWER_TOKEN_ENV_VAR = "SECURITY_DIAGNOSIS_API_REVIEWER_TOKEN"
API_REVIEWER_ACTOR_ENV_VAR = "SECURITY_DIAGNOSIS_API_REVIEWER_ACTOR"
#: 显式开启无认证 Demo；只有显式设置该开关时才允许无认证运行。
API_DEMO_ENV_VAR = "SECURITY_DIAGNOSIS_API_DEMO"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _is_loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def resolve_api_security() -> tuple[str, BearerAuthenticator | None]:
    """解析监听地址与认证器。

    - 正式入口（默认）：**始终**要求分离的 operator/reviewer 双凭证，即使只监听
      回环地址；缺失即拒绝启动；
    - 显式 Demo（``SECURITY_DIAGNOSIS_API_DEMO`` 为真值）：仅允许回环监听，且不得
      同时配置凭证，返回 ``None`` 认证器。
    """
    host = os.environ.get(API_HOST_ENV_VAR, DEFAULT_HOST).strip() or DEFAULT_HOST
    token = os.environ.get(API_TOKEN_ENV_VAR, "")
    actor = os.environ.get(API_ACTOR_ENV_VAR, "api-operator").strip()
    reviewer_token = os.environ.get(API_REVIEWER_TOKEN_ENV_VAR, "")
    reviewer_actor = os.environ.get(API_REVIEWER_ACTOR_ENV_VAR, "api-reviewer").strip()
    demo = os.environ.get(API_DEMO_ENV_VAR, "").strip().lower() in _TRUE_VALUES
    if demo:
        if not _is_loopback(host):
            raise RuntimeConfigurationError("无认证 Demo 只允许监听回环地址")
        if token or reviewer_token:
            raise RuntimeConfigurationError("Demo 模式不得同时配置 operator/reviewer 凭证")
        return host, None
    if not token or not reviewer_token:
        raise RuntimeConfigurationError(
            "正式 API 入口必须同时配置分离的 operator/reviewer 身份认证凭证"
            f"（即使只监听回环地址）；确需无认证 Demo 请显式设置 {API_DEMO_ENV_VAR}"
        )
    try:
        authenticator = BearerAuthenticator.for_separated_duties(
            operator_token=token,
            operator_actor=actor,
            reviewer_token=reviewer_token,
            reviewer_actor=reviewer_actor,
        )
    except ValueError as error:
        raise RuntimeConfigurationError(str(error)) from error
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
            # 只有 agent 显式选择 Demo（authenticator is None）时才进入无认证模式。
            authenticator=authenticator,
            demo_mode=authenticator is None,
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
