"""持久化异常映射的**唯一**公共 helper（Phase 6B-2 审计修复）。

Repository 的公开契约是"不暴露 ORM 异常"，因此所有公开仓储方法
（读与写）都通过本模块的 `translate_persistence_error` 统一映射，
避免每个方法复制一套 try/except。

严格边界：

- 只拦截 `SQLAlchemyError`（ORM / DBAPI 层故障）；
- `ConcurrentUpdateError` / `*NotFoundError` / `*AlreadyExistsError`
  是**受控业务结果**，必须原样向上，不能被包装成持久化错误；
- `DomainError` / Pydantic 校验错误 / 任何 Python 编程错误原样抛出，绝不吞掉。
"""

from __future__ import annotations

from security_diagnosis_harness.application.errors import RepositoryPersistenceError


def translate_persistence_error(entity: str, error: BaseException) -> RepositoryPersistenceError:
    """把 SQLAlchemy 异常映射为受控的 `RepositoryPersistenceError`。

    调用方负责在映射前完成 `rollback()`。只应在
    `except SQLAlchemyError` 分支中调用，因此入参类型在静态层面已知。
    """
    return RepositoryPersistenceError(entity, type(error).__name__)


__all__ = ["translate_persistence_error"]
