"""诊断与知识聚合的只读一致性扫描。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from security_diagnosis_harness.application.errors import RepositoryPersistenceError
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidateStatus
from security_diagnosis_harness.domain.review import HumanReviewAction
from security_diagnosis_harness.ports.diagnosis_repository import DiagnosisRepository
from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRepository


class FindingSeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


class ConsistencyFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    severity: FindingSeverity
    entity_type: str
    entity_id: str
    message: str


class ConsistencyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    diagnosis_count: int
    knowledge_count: int
    findings: list[ConsistencyFinding] = Field(default_factory=list)

    @property
    def blocking_count(self) -> int:
        return sum(item.severity is FindingSeverity.BLOCKING for item in self.findings)

    @property
    def ok(self) -> bool:
        return self.blocking_count == 0

    def to_markdown(self) -> str:
        lines = [
            "# 数据一致性扫描报告",
            "",
            f"- 诊断数：{self.diagnosis_count}",
            f"- 知识数：{self.knowledge_count}",
            f"- 阻塞问题：{self.blocking_count}",
            f"- 结论：{'通过' if self.ok else '未通过'}",
            "",
            "## Findings",
            "",
        ]
        if not self.findings:
            lines.append("无异常。")
        else:
            lines.extend(
                f"- [{item.severity.value}] `{item.code}` {item.entity_type}/"
                f"{item.entity_id}: {item.message}"
                for item in self.findings
            )
        return "\n".join(lines) + "\n"


class ConsistencyScanner:
    """只读扫描器；不调用任何 Repository 写方法。"""

    def __init__(self, diagnoses: DiagnosisRepository, knowledge: KnowledgeRepository) -> None:
        self._diagnoses = diagnoses
        self._knowledge = knowledge

    def scan(self) -> ConsistencyReport:
        findings: list[ConsistencyFinding] = []
        try:
            cases = self._diagnoses.list()
        except (RepositoryPersistenceError, ValidationError, ValueError):
            cases = []
            findings.append(
                _finding(
                    "diagnosis_repository_unreadable",
                    "repository",
                    "diagnosis_cases",
                    "诊断聚合无法完整重建，需要人工检查原始数据",
                )
            )
        try:
            candidates = self._knowledge.list_all()
        except (RepositoryPersistenceError, ValidationError, ValueError):
            candidates = []
            findings.append(
                _finding(
                    "knowledge_repository_unreadable",
                    "repository",
                    "knowledge_candidates",
                    "知识聚合无法完整重建，需要人工检查原始数据",
                )
            )
        case_by_id = {item.diagnosis_id: item for item in cases}

        for case in cases:
            if case.version < 1:
                findings.append(_finding("invalid_version", "diagnosis", case.diagnosis_id))
            known = {item.evidence_id for item in case.evidence}
            if case.conclusion is not None:
                missing = set(case.conclusion.cited_evidence_ids) - known
                if missing:
                    findings.append(
                        _finding(
                            "unknown_evidence_reference",
                            "diagnosis",
                            case.diagnosis_id,
                            f"结论引用不存在的 Evidence: {sorted(missing)}",
                        )
                    )
                if case.conclusion.diagnosis_id != case.diagnosis_id:
                    findings.append(
                        _finding(
                            "cross_diagnosis_conclusion", "diagnosis", case.diagnosis_id
                        )
                    )
                if case.conclusion.fault_type is not case.fault_type:
                    findings.append(
                        _finding("cross_fault_conclusion", "diagnosis", case.diagnosis_id)
                    )
            if case.status is SecurityDiagnosisStatus.CONFIRMED and not any(
                review.action is HumanReviewAction.CONFIRM for review in case.reviews
            ):
                findings.append(
                    _finding(
                        "confirmed_without_human_review", "diagnosis", case.diagnosis_id
                    )
                )

        for item in candidates:
            if item.version < 1:
                findings.append(_finding("invalid_version", "knowledge", item.knowledge_id))
            source = case_by_id.get(item.source_diagnosis_id)
            if source is None:
                findings.append(
                    _finding("missing_source_diagnosis", "knowledge", item.knowledge_id)
                )
                continue
            if source.status is not SecurityDiagnosisStatus.CONFIRMED:
                findings.append(
                    _finding(
                        "source_diagnosis_not_confirmed", "knowledge", item.knowledge_id
                    )
                )
            if source.fault_type is not item.fault_type:
                findings.append(
                    _finding("source_fault_mismatch", "knowledge", item.knowledge_id)
                )
            if (
                source.conclusion is None
                or source.conclusion.conclusion_id != item.source_conclusion_id
            ):
                findings.append(
                    _finding("missing_source_conclusion", "knowledge", item.knowledge_id)
                )
            source_evidence = {evidence.evidence_id for evidence in source.evidence}
            if not set(item.source_evidence_ids).issubset(source_evidence):
                findings.append(_finding("missing_source_evidence", "knowledge", item.knowledge_id))
            if item.status is KnowledgeCandidateStatus.CONFIRMED and not any(
                review.action.value == "confirm" for review in item.reviews
            ):
                findings.append(
                    _finding(
                        "knowledge_confirmed_without_review",
                        "knowledge",
                        item.knowledge_id,
                    )
                )

        return ConsistencyReport(
            diagnosis_count=len(cases),
            knowledge_count=len(candidates),
            findings=findings,
        )


def _finding(code: str, entity_type: str, entity_id: str, message: str = "") -> ConsistencyFinding:
    return ConsistencyFinding(
        code=code,
        severity=FindingSeverity.BLOCKING,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message or code.replace("_", " "),
    )
