"""应用层异常。"""


class ApplicationError(Exception):
    """应用层受控错误基类。"""


class DiagnosisNotFoundError(ApplicationError):
    """诊断不存在。"""

    def __init__(self, diagnosis_id: str) -> None:
        super().__init__(f"诊断 {diagnosis_id} 不存在")
        self.diagnosis_id = diagnosis_id


class DiagnosisAlreadyExistsError(ApplicationError):
    """诊断 ID 已存在（重复 save 的受控失败）。"""

    def __init__(self, diagnosis_id: str) -> None:
        super().__init__(f"诊断 {diagnosis_id} 已存在")
        self.diagnosis_id = diagnosis_id


class KnowledgeNotFoundError(ApplicationError):
    """知识候选不存在（Repository 统一契约）。"""

    def __init__(self, knowledge_id: str) -> None:
        super().__init__(f"知识 {knowledge_id} 不存在")
        self.knowledge_id = knowledge_id


class KnowledgeAlreadyExistsError(ApplicationError):
    """知识 ID 已存在（重复 save 的受控失败）。"""

    def __init__(self, knowledge_id: str) -> None:
        super().__init__(f"知识 {knowledge_id} 已存在")
        self.knowledge_id = knowledge_id


class AuditEventNotFoundError(ApplicationError):
    """审计事件不存在。"""

    def __init__(self, event_id: str) -> None:
        super().__init__(f"审计事件 {event_id} 不存在")
        self.event_id = event_id


class ConcurrentUpdateError(ApplicationError):
    """乐观锁冲突：陈旧副本试图覆盖较新的持久化状态。

    内部保留 entity_type / entity_id / expected_version 供上层排查，
    但 API 只返回安全文案，不暴露 expected / actual version。
    """

    def __init__(self, entity_type: str, entity_id: str, expected_version: int) -> None:
        super().__init__(
            f"{entity_type} {entity_id} 已被其他请求更新，请重新读取后再操作"
        )
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.expected_version = expected_version


class RepositoryPersistenceError(ApplicationError):
    """无法归类的持久化写入失败。

    用于把底层数据库异常（IntegrityError / OperationalError 等）
    统一映射为应用层受控错误，避免 ORM 细节泄漏到 Application / API。
    """

    def __init__(self, entity: str, reason: str = "") -> None:
        message = f"{entity} 持久化失败"
        if reason:
            message = f"{message}：{reason}"
        super().__init__(message)
        self.entity = entity
        self.reason = reason


class UnsupportedFaultTypeError(ApplicationError):
    """当前 Runtime 未装配该故障类型的诊断能力。

    Phase 6B-1 尚未实现四故障域统一路由，正式 Runtime 只支持已装配的故障类型；
    其余类型必须在 create 与 run 两处都被拒绝。
    """

    def __init__(self, fault_type: str) -> None:
        super().__init__(f"当前运行环境暂不支持故障类型 {fault_type} 的诊断")
        self.fault_type = fault_type


class KnowledgeCandidateGenerationError(ApplicationError):
    """当前诊断不满足生成知识候选的前置条件。"""
