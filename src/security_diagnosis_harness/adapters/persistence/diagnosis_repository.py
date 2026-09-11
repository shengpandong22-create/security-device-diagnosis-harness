"""SQLite 诊断仓储实现。

行为与 `InMemoryDiagnosisRepository` 对齐：

- `save()`：新增；ID 已存在时抛 `DiagnosisAlreadyExistsError`（受控失败，不覆盖）；
- `update()`：更新已存在；ID 不存在时抛 `DiagnosisNotFoundError`；
- `get()`：不存在时抛 `DiagnosisNotFoundError`；
- `list()`：按创建时间与 ID 稳定排序。

读出的对象都是重新构造的 Domain 实例，调用方本地修改不会隐式写回数据库，
必须显式调用 `update()`。本层不依赖底层 SQLAlchemy 异常作为公开契约。
"""

from __future__ import annotations

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.persistence.mapping import (
    case_to_columns,
    columns_to_case,
)
from security_diagnosis_harness.adapters.persistence.models import DiagnosisCaseRow
from security_diagnosis_harness.application.errors import (
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
    RepositoryPersistenceError,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase

_ENTITY = "诊断"


class SqlAlchemyDiagnosisRepository:
    """基于 SQLAlchemy 2.x 的诊断仓储。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemyDiagnosisRepository:
        """从 Engine 构造（自动建 session 工厂）。"""
        return cls(sessionmaker(bind=engine, expire_on_commit=False, future=True))

    # ------------------------------------------------------------------ 写
    def save(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        """新增一条诊断；ID 重复时受控失败。

        不做 `select exists -> insert` 的竞态检查，直接插入并依赖主键约束：
        捕获 `IntegrityError` 后映射为 `DiagnosisAlreadyExistsError`。
        """
        with self._session_factory() as session:
            session.add(DiagnosisCaseRow(**case_to_columns(case)))
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise DiagnosisAlreadyExistsError(case.diagnosis_id) from exc
            except SQLAlchemyError as exc:
                session.rollback()
                raise RepositoryPersistenceError(_ENTITY, type(exc).__name__) from exc
        return self.get(case.diagnosis_id)

    def update(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        """更新一条已存在诊断；不存在时受控失败。"""
        with self._session_factory() as session:
            row = session.get(DiagnosisCaseRow, case.diagnosis_id)
            if row is None:
                raise DiagnosisNotFoundError(case.diagnosis_id)
            for key, value in case_to_columns(case).items():
                setattr(row, key, value)
            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise RepositoryPersistenceError(_ENTITY, type(exc).__name__) from exc
        return self.get(case.diagnosis_id)

    # ------------------------------------------------------------------ 读
    def get(self, diagnosis_id: str) -> SecurityDiagnosisCase:
        with self._session_factory() as session:
            row = session.get(DiagnosisCaseRow, diagnosis_id)
            if row is None:
                raise DiagnosisNotFoundError(diagnosis_id)
            return columns_to_case(row)

    def list(self) -> list[SecurityDiagnosisCase]:
        statement = select(DiagnosisCaseRow).order_by(
            DiagnosisCaseRow.created_at, DiagnosisCaseRow.diagnosis_id
        )
        with self._session_factory() as session:
            return [columns_to_case(row) for row in session.scalars(statement)]

    def exists(self, diagnosis_id: str) -> bool:
        with self._session_factory() as session:
            return session.get(DiagnosisCaseRow, diagnosis_id) is not None

    def count(self) -> int:
        """用 SQL COUNT 统计，不加载全部主键。"""
        with self._session_factory() as session:
            return int(session.scalar(select(func.count()).select_from(DiagnosisCaseRow)) or 0)


__all__ = ["SqlAlchemyDiagnosisRepository"]
