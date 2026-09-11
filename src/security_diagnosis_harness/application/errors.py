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


class KnowledgeCandidateGenerationError(ApplicationError):
    """当前诊断不满足生成知识候选的前置条件。"""
