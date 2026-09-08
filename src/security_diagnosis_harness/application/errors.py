"""应用层异常。"""


class ApplicationError(Exception):
    """应用层受控错误基类。"""


class DiagnosisNotFoundError(ApplicationError):
    """诊断不存在。"""

    def __init__(self, diagnosis_id: str) -> None:
        super().__init__(f"诊断 {diagnosis_id} 不存在")
        self.diagnosis_id = diagnosis_id
