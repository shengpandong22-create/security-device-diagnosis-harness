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
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.api.app import create_app  # noqa: E402
from security_diagnosis_harness.config import build_runtime_settings  # noqa: E402
from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def build_app():
    """构建 (app, runtime)；调用方负责在退出时 `runtime.close()`。"""
    settings = build_runtime_settings()
    runtime = build_runtime_container(settings)
    app = create_app(
        runtime.service,
        repository_mode=settings.repository_mode.value,
        database_ready=runtime.database_ready,
    )
    return app, runtime


def main() -> int:
    import uvicorn

    # 迁移失败会在这里抛出，不返回可用的 service。
    app, runtime = build_app()
    try:
        uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT, reload=False)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
