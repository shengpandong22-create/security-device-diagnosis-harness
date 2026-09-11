"""知识仓储适配器。"""

from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
    KnowledgeNotFoundError,
)

__all__ = ["InMemoryKnowledgeRepository", "KnowledgeNotFoundError"]
