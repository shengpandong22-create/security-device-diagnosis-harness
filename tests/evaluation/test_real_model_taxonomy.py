from __future__ import annotations

import pytest

from security_diagnosis_harness.application.access_diagnosis_rules import (
    AccessDiagnosisLabel,
)
from security_diagnosis_harness.application.alarm_diagnosis_rules import (
    AlarmDiagnosisLabel,
)
from security_diagnosis_harness.application.camera_diagnosis_rules import (
    CameraDiagnosisLabel,
)
from security_diagnosis_harness.application.recording_diagnosis_rules import (
    RecordingDiagnosisLabel,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.evaluation import evaluation_taxonomy


@pytest.mark.parametrize(
    ("fault_type", "label_type"),
    [
        (SecurityFaultType.CAMERA_BLACK_SCREEN, CameraDiagnosisLabel),
        (SecurityFaultType.RECORDING_MISSING, RecordingDiagnosisLabel),
        (SecurityFaultType.ACCESS_CARD_FAILED, AccessDiagnosisLabel),
        (SecurityFaultType.ALARM_FALSE_POSITIVE, AlarmDiagnosisLabel),
    ],
)
def test_taxonomy_exposes_complete_domain_label_set(fault_type, label_type):
    taxonomy = evaluation_taxonomy(fault_type)
    assert set(taxonomy.candidate_labels) == {item.value for item in label_type}


@pytest.mark.parametrize("fault_type", list(SecurityFaultType))
def test_taxonomy_uses_only_canonical_evidence_types(fault_type):
    taxonomy = evaluation_taxonomy(fault_type)
    assert taxonomy.evidence_types
    assert all(isinstance(item, EvidenceType) for item in taxonomy.evidence_types)
    assert len(taxonomy.evidence_types) == len(set(taxonomy.evidence_types))
