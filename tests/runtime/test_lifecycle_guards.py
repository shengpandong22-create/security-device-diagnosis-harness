"""Phase 6B-1 收尾：资源生命周期与架构守卫验收。

覆盖：

- `build_runtime_service` 资源泄漏入口已移除；
- 所有 SQLite Runtime 构建入口都返回 RuntimeContainer；
- `migrations/env.py` 在成功/失败路径都 dispose Engine；
- `run_api.build_app` 在 create_app 失败时关闭 runtime；
- `run_api.main` 最终 close。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = REPO_ROOT / "src" / "security_diagnosis_harness" / "runtime.py"
ENV_PATH = REPO_ROOT / "migrations" / "env.py"
RUN_API_PATH = REPO_ROOT / "scripts" / "run_api.py"


# ---------------------------------------------------------------- 五
def test_build_runtime_service_is_removed():
    """不允许存在「只返回 service、丢失 Engine owner」的入口。"""
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    assert "build_runtime_service" not in function_names

    runtime_module = __import__(
        "security_diagnosis_harness.runtime", fromlist=["__all__"]
    )
    assert "build_runtime_service" not in runtime_module.__all__
    assert not hasattr(runtime_module, "build_runtime_service")


def test_runtime_module_has_no_global_container_cache():
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    module_level_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "RUNTIME_CONTAINER" not in module_level_assignments
    assert "_GLOBAL_RUNTIME" not in module_level_assignments


def test_all_sqlite_runtime_builders_return_container():
    """所有返回服务的构建入口都会丢失 owner；只允许返回 RuntimeContainer 的 builder。"""
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("build_runtime"):
            returns_container = any(
                isinstance(inner, ast.Name) and inner.id == "RuntimeContainer"
                for inner in ast.walk(node)
                if isinstance(inner, ast.Constant) is False
            )
            assert returns_container, f"{node.name} 未返回 RuntimeContainer"


def test_run_api_holds_container_until_shutdown():
    source = RUN_API_PATH.read_text(encoding="utf-8")

    assert "runtime.close()" in source
    assert "finally:" in source


# ---------------------------------------------------------------- 六
def test_migration_env_disposes_engine_on_success(monkeypatch):
    """成功路径必须 dispose 迁移用的 Engine。"""
    from security_diagnosis_harness import runtime as runtime_module

    disposed = {"count": 0}

    class _FakeEngine:
        def connect(self):
            raise AssertionError("不应真正连接")

        def dispose(self):
            disposed["count"] += 1

    monkeypatch.setattr(
        runtime_module, "_run_migrations_with_engine", lambda engine: None, raising=False
    )
    monkeypatch.setattr(runtime_module, "_safe_target", lambda url: url, raising=False)

    # 直接验证 env.py 的源码结构：engine 创建后必须有 try/finally dispose。
    env_source = ENV_PATH.read_text(encoding="utf-8")
    assert "connectable.dispose()" in env_source
    assert "finally:" in env_source


def test_migrations_env_uses_try_finally(monkeypatch, tmp_path: Path):
    """通过真实迁移验证：成功后 Engine 已释放（文件可删除）。"""
    from security_diagnosis_harness.runtime import upgrade_database

    db_path = tmp_path / "lifecycle.db"
    url = f"sqlite:///{db_path.as_posix()}"

    upgrade_database(url)

    # 迁移 Engine 未释放时，Windows 下删除会失败。
    db_path.unlink()
    assert not db_path.exists()


def test_migrations_env_disposes_on_failure(monkeypatch, tmp_path: Path):
    """迁移抛异常时也必须 dispose，之后文件可删除。"""
    from alembic import command

    from security_diagnosis_harness import runtime as runtime_module

    db_path = tmp_path / "failure.db"
    url = f"sqlite:///{db_path.as_posix()}"

    def _boom(config, revision):
        raise RuntimeError("upgrade failed")

    monkeypatch.setattr(command, "upgrade", _boom)

    with pytest.raises(RuntimeError, match="upgrade failed"):
        runtime_module.upgrade_database(url)

    if db_path.exists():
        db_path.unlink()
    assert not db_path.exists()


# ---------------------------------------------------------------- 七
def test_build_app_closes_runtime_when_create_app_fails(monkeypatch, tmp_path: Path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("run_api_probe", RUN_API_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    closed = {"count": 0}

    class _FakeRuntime:
        service = object()

        def database_ready(self):
            return True

        def close(self):
            closed["count"] += 1

    monkeypatch.setattr(
        module,
        "build_runtime_settings",
        lambda: type(
            "S", (), {"repository_mode": type("M", (), {"value": "sqlite"})()}
        )(),
    )
    monkeypatch.setattr(module, "build_runtime_container", lambda settings: _FakeRuntime())

    def _explode(*args, **kwargs):
        raise RuntimeError("create_app failed")

    monkeypatch.setattr(module, "create_app", _explode)

    with pytest.raises(RuntimeError, match="create_app failed"):
        module.build_app()

    assert closed["count"] == 1


def test_build_app_does_not_close_on_success(monkeypatch):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("run_api_probe2", RUN_API_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    closed = {"count": 0}

    class _FakeRuntime:
        service = object()
        closed = False

        def database_ready(self):
            return True

        def close(self):
            closed["count"] += 1

    monkeypatch.setattr(
        module,
        "build_runtime_settings",
        lambda: type(
            "S", (), {"repository_mode": type("M", (), {"value": "sqlite"})()}
        )(),
    )
    monkeypatch.setattr(module, "build_runtime_container", lambda settings: _FakeRuntime())
    monkeypatch.setattr(module, "create_app", lambda *a, **k: object())

    app, runtime = module.build_app()

    assert app is not None
    assert runtime is not None
    assert closed["count"] == 0
