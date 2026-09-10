"""应用编排层：用例边界、状态推进与报告渲染。"""

from security_diagnosis_harness.application.access_diagnosis_rules import (
    AccessDiagnosisLabel,
    AccessDiagnosisRuleResult,
    AccessFacts,
    extract_access_facts,
    infer_access_card_failed_label,
)
from security_diagnosis_harness.application.camera_diagnosis_rules import (
    CameraDiagnosisLabel,
    CameraDiagnosisRuleResult,
    CameraFacts,
    extract_camera_facts,
    infer_camera_black_screen_label,
)
from security_diagnosis_harness.application.diagnoses import (
    CitationRepair,
    RunDiagnosisResult,
    SecurityDiagnosisApplicationService,
    device_fact_evidence_ids,
    missing_device_fact_evidence_ids,
    repair_cited_evidence_ids,
)
from security_diagnosis_harness.application.errors import (
    ApplicationError,
    DiagnosisNotFoundError,
    KnowledgeCandidateGenerationError,
)
from security_diagnosis_harness.application.knowledge_candidates import (
    KnowledgeCandidateApplicationService,
)
from security_diagnosis_harness.application.reports import render_markdown_report
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository

__all__ = [
    "AccessDiagnosisLabel",
    "AccessDiagnosisRuleResult",
    "AccessFacts",
    "ApplicationError",
    "CameraDiagnosisLabel",
    "CameraDiagnosisRuleResult",
    "CameraFacts",
    "CitationRepair",
    "DiagnosisNotFoundError",
    "InMemoryDiagnosisRepository",
    "KnowledgeCandidateApplicationService",
    "KnowledgeCandidateGenerationError",
    "RunDiagnosisResult",
    "SecurityDiagnosisApplicationService",
    "device_fact_evidence_ids",
    "extract_access_facts",
    "extract_camera_facts",
    "infer_access_card_failed_label",
    "infer_camera_black_screen_label",
    "missing_device_fact_evidence_ids",
    "render_markdown_report",
    "repair_cited_evidence_ids",
]
