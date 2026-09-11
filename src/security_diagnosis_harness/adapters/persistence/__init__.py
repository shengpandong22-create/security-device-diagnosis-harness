"""Phase 6A SQLite 持久化适配器。

对外暴露：

- `build_database(...)`：一次性构造 Engine + Session 工厂；
- `SqlAlchemyDiagnosisRepository` / `SqlAlchemyKnowledgeRepository`：仓储实现；
- `Base`：供 Alembic 迁移与 metadata 一致性校验使用。
"""

from security_diagnosis_harness.adapters.persistence.audit_repository import (
    AuditEventNotFoundError,
    SqlAlchemyAuditRepository,
)
from security_diagnosis_harness.adapters.persistence.database import (
    DATABASE_URL_ENV_VAR,
    DEFAULT_DATABASE_URL,
    build_database,
    build_engine,
    build_session_factory,
    ensure_sqlite_directory,
    resolve_database_url,
)
from security_diagnosis_harness.adapters.persistence.diagnosis_repository import (
    SqlAlchemyDiagnosisRepository,
)
from security_diagnosis_harness.adapters.persistence.knowledge_repository import (
    SqlAlchemyKnowledgeRepository,
)
from security_diagnosis_harness.adapters.persistence.models import (
    AuditEventRow,
    Base,
    DiagnosisCaseRow,
    KnowledgeCandidateRow,
)

__all__ = [
    "DATABASE_URL_ENV_VAR",
    "DEFAULT_DATABASE_URL",
    "Base",
    "AuditEventRow",
    "AuditEventNotFoundError",
    "DiagnosisCaseRow",
    "KnowledgeCandidateRow",
    "SqlAlchemyDiagnosisRepository",
    "SqlAlchemyAuditRepository",
    "SqlAlchemyKnowledgeRepository",
    "build_database",
    "build_engine",
    "build_session_factory",
    "ensure_sqlite_directory",
    "resolve_database_url",
]
