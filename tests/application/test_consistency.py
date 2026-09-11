"""Phase 6C-2 只读一致性扫描验收。"""

from security_diagnosis_harness.application.consistency import ConsistencyScanner
from security_diagnosis_harness.application.errors import RepositoryPersistenceError
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidateStatus
from tests.persistence._builders import build_confirmed_case, build_knowledge_candidate


class _DiagnosisRepo:
    def __init__(self, items):
        self.items = items
        self.write_calls = 0

    def list(self):
        return self.items

    def save(self, item):
        self.write_calls += 1

    def update(self, item):
        self.write_calls += 1

    def get(self, item_id):
        return next(item for item in self.items if item.diagnosis_id == item_id)

    def exists(self, item_id):
        return any(item.diagnosis_id == item_id for item in self.items)

    def count(self):
        return len(self.items)


class _KnowledgeRepo:
    def __init__(self, items):
        self.items = items
        self.write_calls = 0

    def list_all(self):
        return self.items

    def save(self, item):
        self.write_calls += 1

    def update(self, item):
        self.write_calls += 1

    def get(self, item_id):
        return next(item for item in self.items if item.knowledge_id == item_id)

    def search_confirmed(self, query, fault_type, limit=3):
        return []


def _scanner(cases, knowledge):
    diagnosis_repo = _DiagnosisRepo(cases)
    knowledge_repo = _KnowledgeRepo(knowledge)
    return ConsistencyScanner(diagnosis_repo, knowledge_repo), diagnosis_repo, knowledge_repo


def test_normal_linked_data_has_no_blocking_findings():
    case = build_confirmed_case().model_copy(update={"version": 1})
    item = build_knowledge_candidate().model_copy(
        update={
            "source_diagnosis_id": case.diagnosis_id,
            "source_conclusion_id": case.conclusion.conclusion_id,
            "source_evidence_ids": list(case.conclusion.cited_evidence_ids),
            "version": 1,
        }
    )
    report, _, _ = _scanner([case], [item])
    result = report.scan()
    assert result.ok is True
    assert result.findings == []


def test_finds_unknown_evidence_reference():
    case = build_confirmed_case().model_copy(deep=True, update={"version": 1})
    case.conclusion.cited_evidence_ids.append("missing-evidence")
    scanner, _, _ = _scanner([case], [])
    assert "unknown_evidence_reference" in {item.code for item in scanner.scan().findings}


def test_finds_confirmed_without_human_review():
    case = build_confirmed_case().model_copy(
        deep=True,
        update={"version": 1, "reviews": [], "status": SecurityDiagnosisStatus.CONFIRMED},
    )
    scanner, _, _ = _scanner([case], [])
    assert "confirmed_without_human_review" in {item.code for item in scanner.scan().findings}


def test_finds_invalid_versions():
    case = build_confirmed_case().model_copy(update={"version": 0})
    item = build_knowledge_candidate().model_copy(update={"version": 0})
    scanner, _, _ = _scanner([case], [item])
    codes = [finding.code for finding in scanner.scan().findings]
    assert codes.count("invalid_version") == 2


def test_finds_knowledge_source_breaks():
    item = build_knowledge_candidate().model_copy(update={"version": 1})
    scanner, _, _ = _scanner([], [item])
    assert "missing_source_diagnosis" in {entry.code for entry in scanner.scan().findings}


def test_finds_source_conclusion_and_evidence_breaks():
    case = build_confirmed_case().model_copy(update={"version": 1})
    item = build_knowledge_candidate().model_copy(
        update={
            "version": 1,
            "source_diagnosis_id": case.diagnosis_id,
            "source_conclusion_id": "missing-conclusion",
            "source_evidence_ids": ["missing-evidence"],
        }
    )
    scanner, _, _ = _scanner([case], [item])
    codes = {entry.code for entry in scanner.scan().findings}
    assert {"missing_source_conclusion", "missing_source_evidence"} <= codes


def test_finds_confirmed_knowledge_without_review():
    case = build_confirmed_case().model_copy(update={"version": 1})
    item = build_knowledge_candidate().model_copy(
        update={
            "version": 1,
            "status": KnowledgeCandidateStatus.CONFIRMED,
            "reviews": [],
            "source_diagnosis_id": case.diagnosis_id,
            "source_conclusion_id": case.conclusion.conclusion_id,
            "source_evidence_ids": list(case.conclusion.cited_evidence_ids),
        }
    )
    scanner, _, _ = _scanner([case], [item])
    assert "knowledge_confirmed_without_review" in {
        entry.code for entry in scanner.scan().findings
    }


def test_scanner_never_calls_write_methods():
    scanner, diagnoses, knowledge = _scanner([], [])
    scanner.scan()
    assert diagnoses.write_calls == 0
    assert knowledge.write_calls == 0


def test_markdown_report_is_actionable():
    scanner, _, _ = _scanner([], [])
    markdown = scanner.scan().to_markdown()
    assert "数据一致性扫描报告" in markdown
    assert "结论：通过" in markdown


def test_corrupt_repository_becomes_blocking_finding():
    class BrokenDiagnosisRepository(_DiagnosisRepo):
        def list(self):
            raise RepositoryPersistenceError("诊断", "corrupt payload")

    scanner = ConsistencyScanner(BrokenDiagnosisRepository([]), _KnowledgeRepo([]))
    report = scanner.scan()
    assert report.ok is False
    assert [item.code for item in report.findings] == [
        "diagnosis_repository_unreadable"
    ]


def test_finds_unconfirmed_and_cross_fault_knowledge_source():
    case = build_confirmed_case().model_copy(
        update={"version": 1, "status": SecurityDiagnosisStatus.REJECTED}
    )
    item = build_knowledge_candidate().model_copy(
        update={
            "version": 1,
            "fault_type": "recording_missing",
            "source_diagnosis_id": case.diagnosis_id,
            "source_conclusion_id": case.conclusion.conclusion_id,
            "source_evidence_ids": list(case.conclusion.cited_evidence_ids),
        }
    )
    scanner, _, _ = _scanner([case], [item])
    codes = {entry.code for entry in scanner.scan().findings}
    assert {"source_diagnosis_not_confirmed", "source_fault_mismatch"} <= codes


def test_finds_cross_diagnosis_and_cross_fault_conclusion():
    case = build_confirmed_case().model_copy(deep=True, update={"version": 1})
    case.conclusion.diagnosis_id = "another-diagnosis"
    case.conclusion.fault_type = "recording_missing"
    scanner, _, _ = _scanner([case], [])
    codes = {entry.code for entry in scanner.scan().findings}
    assert {"cross_diagnosis_conclusion", "cross_fault_conclusion"} <= codes
