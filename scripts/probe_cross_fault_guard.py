"""跨故障域护栏探针（Phase 6B-1 收尾）。

用正式 RuntimeContainer 真实执行以下对抗场景：

1. 摄像头 Runtime 尝试创建录像诊断；
2. 摄像头 Case 尝试接受录像结论；
3. 被拒绝后检查 Case 是否被就地修改。

输出：

```text
RECORDING_CASE_ACCEPTED_BY_CAMERA_RUNTIME: False
CROSS_FAULT_CONCLUSION_ACCEPTED: False
CASE_STATE_MUTATED_AFTER_REJECTION: False
```
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.application.errors import (  # noqa: E402
    UnsupportedFaultTypeError,
)
from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase  # noqa: E402
from security_diagnosis_harness.domain.citation_policy import CitationPolicy  # noqa: E402
from security_diagnosis_harness.domain.conclusion import (  # noqa: E402
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.enums import (  # noqa: E402
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.errors import (  # noqa: E402
    ConclusionFaultTypeMismatch,
)
from security_diagnosis_harness.domain.evidence import (  # noqa: E402
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
)
from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402


def _recording_case() -> SecurityDiagnosisCase:
    case = SecurityDiagnosisCase(
        diagnosis_id="probe-diag-cross",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id="cam-1",
        reporter="probe",
    )
    case.add_evidence(
        DiagnosisEvidence(
            evidence_id="probe-evd-status",
            diagnosis_id=case.diagnosis_id,
            evidence_type=EvidenceType.DEVICE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="设备离线",
        )
    )
    case.add_evidence(
        DiagnosisEvidence(
            evidence_id="probe-evd-config",
            diagnosis_id=case.diagnosis_id,
            evidence_type=EvidenceType.DEVICE_CONFIG,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="配置快照",
        )
    )
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    return case


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="phase6b-cross-"))
    settings = RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(workdir / 'cross.db').as_posix()}",
        auto_migrate=True,
    )

    recording_accepted = False
    cross_fault_accepted = False
    state_mutated = False

    runtime = None
    try:
        runtime = build_runtime_container(settings)

        # -------------------------------------------------- 场景 1：跨域 create
        try:
            runtime.service.create_diagnosis(
                device_id="cam-rec-plan-disabled-01",
                fault_type=SecurityFaultType.RECORDING_MISSING,
                reporter="probe",
            )
            recording_accepted = True
        except UnsupportedFaultTypeError:
            recording_accepted = False

        # -------------------------------------------------- 场景 2：跨域结论
        case = _recording_case()
        before_status = case.status
        before_conclusion = case.conclusion
        before_updated_at = case.updated_at
        cross_conclusion = DiagnosisConclusion(
            conclusion_id="probe-con",
            diagnosis_id=case.diagnosis_id,
            fault_type=SecurityFaultType.RECORDING_MISSING,
            summary="录像缺失结论",
            confidence=ConclusionConfidence.PROBABLE,
            cited_evidence_ids=["probe-evd-status", "probe-evd-config"],
        )

        try:
            case.set_conclusion(cross_conclusion)
            cross_fault_accepted = True
        except ConclusionFaultTypeMismatch:
            cross_fault_accepted = False

        try:
            CitationPolicy().validate(cross_conclusion, case)
            policy_accepted = True
        except ConclusionFaultTypeMismatch:
            policy_accepted = False

        state_mutated = (
            case.status is not before_status
            or case.conclusion is not before_conclusion
            or case.updated_at != before_updated_at
        )
    finally:
        if runtime is not None:
            runtime.close()
        shutil.rmtree(workdir, ignore_errors=True)

    report = {
        "recording_case_accepted_by_camera_runtime": recording_accepted,
        "cross_fault_conclusion_accepted": cross_fault_accepted,
        "citation_policy_accepted_cross_fault": policy_accepted,
        "case_state_mutated_after_rejection": state_mutated,
    }
    print(
        "RECORDING_CASE_ACCEPTED_BY_CAMERA_RUNTIME: "
        f"{report['recording_case_accepted_by_camera_runtime']}"
    )
    print(f"CROSS_FAULT_CONCLUSION_ACCEPTED: {report['cross_fault_conclusion_accepted']}")
    print(f"CITATION_POLICY_ACCEPTED_CROSS_FAULT: {report['citation_policy_accepted_cross_fault']}")
    print(f"CASE_STATE_MUTATED_AFTER_REJECTION: {report['case_state_mutated_after_rejection']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = (
        not recording_accepted
        and not cross_fault_accepted
        and not policy_accepted
        and not state_mutated
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
