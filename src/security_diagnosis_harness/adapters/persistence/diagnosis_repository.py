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

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.persistence.errors import (
    translate_persistence_error,
)
from security_diagnosis_harness.adapters.persistence.mapping import (
    case_to_columns,
    columns_to_case,
)
from security_diagnosis_harness.adapters.persistence.models import DiagnosisCaseRow
from security_diagnosis_harness.application.errors import (
    ConcurrentUpdateError,
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

        只接受全新聚合（`version == 0`），写入后版本为 1。
        不做 `select exists -> insert` 的竞态检查，直接插入并依赖主键约束。
        捕获 `IntegrityError` 后回滚，**再确认目标 ID 是否真的已存在**：

        - 已存在 → `DiagnosisAlreadyExistsError`（主键冲突）；
        - 不存在 → `RepositoryPersistenceError`（NOT NULL / CHECK 等其它完整性错误，
          不能误报成「ID 已存在」）。
        """
        if case.version != 0:
            raise ValueError(
                f"save() 只接受全新聚合（version=0），当前 {case.diagnosis_id} "
                f"的 version={case.version}"
            )

        columns = case_to_columns(case)
        columns["version"] = 1
        with self._session_factory() as session:
            session.add(DiagnosisCaseRow(**columns))
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                try:
                    exists = session.get(DiagnosisCaseRow, case.diagnosis_id) is not None
                except SQLAlchemyError as lookup_exc:
                    raise translate_persistence_error(_ENTITY, lookup_exc) from lookup_exc
                if exists:
                    raise DiagnosisAlreadyExistsError(case.diagnosis_id) from exc
                raise RepositoryPersistenceError(_ENTITY, "integrity") from exc
            except SQLAlchemyError as exc:
                session.rollback()
                raise translate_persistence_error(_ENTITY, exc) from exc
        return self.get(case.diagnosis_id)

    def update(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        """CAS 更新：`WHERE id = ? AND version = ?`。

        - `rowcount == 1` → commit，返回 `version + 1` 的新 Domain 副本；
        - `rowcount == 0` → 先 rollback，再判 ID：
          不存在 → `DiagnosisNotFoundError`；存在 → `ConcurrentUpdateError`；
        - CAS 成功后 `commit()` 失败 → rollback + `RepositoryPersistenceError`，
          不返回已经递增版本的假成功对象。
        """
        if case.version <= 0:
            raise ValueError(
                f"update() 不接受未保存聚合（version>=1），当前 "
                f"{case.diagnosis_id} 的 version={case.version}"
            )

        columns = case_to_columns(case)
        columns.pop("diagnosis_id", None)
        # CAS 目标版本：只在 rowcount == 1 时生效。
        columns["version"] = case.version + 1

        with self._session_factory() as session:
            try:
                result = session.execute(
                    update(DiagnosisCaseRow)
                    .where(
                        DiagnosisCaseRow.diagnosis_id == case.diagnosis_id,
                        DiagnosisCaseRow.version == case.version,
                    )
                    .values(**columns)
                )
                rowcount = result.rowcount
            except SQLAlchemyError as exc:
                # execute 阶段的 ORM 异常同样必须 rollback + 映射，不得外泄。
                session.rollback()
                raise translate_persistence_error(_ENTITY, exc) from exc

            if rowcount != 1:
                session.rollback()
                try:
                    exists = session.get(DiagnosisCaseRow, case.diagnosis_id) is not None
                except SQLAlchemyError as exc:
                    raise translate_persistence_error(_ENTITY, exc) from exc
                if not exists:
                    raise DiagnosisNotFoundError(case.diagnosis_id)
                raise ConcurrentUpdateError(_ENTITY, case.diagnosis_id, case.version)

            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise translate_persistence_error(_ENTITY, exc) from exc
        return self.get(case.diagnosis_id)

    # ------------------------------------------------------------------ 读
    def get(self, diagnosis_id: str) -> SecurityDiagnosisCase:
        try:
            with self._session_factory() as session:
                row = session.get(DiagnosisCaseRow, diagnosis_id)
                if row is None:
                    raise DiagnosisNotFoundError(diagnosis_id)
                return columns_to_case(row)
        except SQLAlchemyError as exc:
            raise translate_persistence_error(_ENTITY, exc) from exc

    def list(self) -> list[SecurityDiagnosisCase]:
        statement = select(DiagnosisCaseRow).order_by(
            DiagnosisCaseRow.created_at, DiagnosisCaseRow.diagnosis_id
        )
        try:
            with self._session_factory() as session:
                return [columns_to_case(row) for row in session.scalars(statement)]
        except SQLAlchemyError as exc:
            raise translate_persistence_error(_ENTITY, exc) from exc

    def exists(self, diagnosis_id: str) -> bool:
        try:
            with self._session_factory() as session:
                return session.get(DiagnosisCaseRow, diagnosis_id) is not None
        except SQLAlchemyError as exc:
            raise translate_persistence_error(_ENTITY, exc) from exc

    def count(self) -> int:
        """用 SQL COUNT 统计，不加载全部主键。"""
        try:
            with self._session_factory() as session:
                total = session.scalar(
                    select(func.count()).select_from(DiagnosisCaseRow)
                )
                return int(total or 0)
        except SQLAlchemyError as exc:
            raise translate_persistence_error(_ENTITY, exc) from exc


__all__ = ["SqlAlchemyDiagnosisRepository"]
