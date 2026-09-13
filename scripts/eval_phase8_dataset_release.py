"""Run the fixed synthetic Phase 8D governed release demonstration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.evaluation import (
    AdjudicationAction,
    AdjudicationDecision,
    AnnotationConfidence,
    BlindAnnotation,
    DatasetCase,
    DatasetRegistry,
    DatasetReleaseAddition,
    DatasetSplit,
    EvaluationBudget,
    ForbiddenBehavior,
    SourceKind,
    SourceProvenance,
    TestSetAccessError,
    adjudicate_annotations,
    build_annotation_task,
    publish_dataset_version,
    write_dataset_release_report,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets/security-diagnosis/1.0.0"


def _fixed_addition() -> DatasetReleaseAddition:
    case = DatasetCase(
        dataset_version="1.1.0",
        split=DatasetSplit.DEV,
        case_id="dev-access-controller-power-loss",
        template_group_id="access-controller-power-release",
        source="synthetic-reviewed-phase8d",
        source_record_id="phase8d-synthetic-001",
        fault_type=SecurityFaultType.ACCESS_CARD_FAILED,
        input_facts={
            "device_id": "phase8d-controller-01",
            "controller_online": False,
            "power_indicator": "off",
            "network_probe": "unreachable",
            "event_result": "no_response",
        },
        allowed_tools=(
            "access__query_controller", "access__query_credential",
            "access__query_policy", "access__search_events",
        ),
        expected_tools=("access__query_controller", "access__search_events"),
        expected_candidate="controller_offline_or_no_response",
        required_evidence_types=(EvidenceType.ACCESS_CONTROLLER, EvidenceType.ACCESS_EVENT),
        forbidden_behaviors=(
            ForbiddenBehavior.AUTO_CONFIRM, ForbiddenBehavior.UNCITED_CONCLUSION,
            ForbiddenBehavior.SENSITIVE_DATA_LEAK,
        ),
        budget=EvaluationBudget(
            max_rounds=4, max_tool_calls=4, timeout_seconds=15, max_model_calls=0
        ),
    )
    task = build_annotation_task(case).model_copy(update={"task_id": "phase8d-task-001"})
    annotation_values = {
        "task_id": task.task_id,
        "candidate_label": case.expected_candidate,
        "necessary_tools": case.expected_tools,
        "necessary_evidence_types": case.required_evidence_types,
        "rationale": "设备离线事实支持控制器无响应候选",
        "confidence": AnnotationConfidence.HIGH,
    }
    first = BlindAnnotation(
        annotation_id="phase8d-annotation-a", reviewer="phase8d-reviewer-a", **annotation_values
    )
    second = BlindAnnotation(
        annotation_id="phase8d-annotation-b", reviewer="phase8d-reviewer-b", **annotation_values
    )
    decision = AdjudicationDecision(
        adjudication_id="phase8d-adjudication-001",
        task_id=task.task_id,
        first_annotation_id=first.annotation_id,
        second_annotation_id=second.annotation_id,
        adjudicator="phase8d-review-lead",
        action=AdjudicationAction.APPROVE,
        final_candidate_label=case.expected_candidate,
        final_tools=case.expected_tools,
        final_evidence_types=case.required_evidence_types,
        rationale="独立裁决确认该合成协议案例",
    )
    candidate = adjudicate_annotations(task, first, second, decision).model_copy(
        update={"candidate_id": "phase8d-candidate-001"}
    )
    return DatasetReleaseAddition(
        proposed_case=case,
        task=task,
        first_annotation=first,
        second_annotation=second,
        decision=decision,
        admission_candidate=candidate,
        provenance=SourceProvenance(
            kind=SourceKind.SYNTHETIC,
            source_record_id=case.source_record_id,
            authorization_reference="phase8d-protocol-fixture",
            authorized_by="dataset-governance",
            reviewed_at=datetime(2026, 9, 13, tzinfo=UTC),
        ),
    )


def main() -> int:
    with TemporaryDirectory(prefix="phase8d-") as directory:
        target, receipt = publish_dataset_version(
            SOURCE,
            Path(directory),
            "1.1.0",
            (_fixed_addition(),),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )
        registry = DatasetRegistry.load(target)
        split_counts = {split.value: registry.case_count(split) for split in DatasetSplit}
        try:
            registry.cases(DatasetSplit.TEST)
        except TestSetAccessError:
            test_set_remains_sealed = True
        else:
            test_set_remains_sealed = False
    json_path, markdown_path = write_dataset_release_report(receipt, ROOT / "demo-output")
    print(
        json.dumps(
            {
                "released_version": receipt.released_version,
                "total_case_count": receipt.total_case_count,
                "split_counts": split_counts,
                "synthetic_case_count": receipt.synthetic_case_count,
                "authorized_case_count": receipt.authorized_case_count,
                "test_set_remains_sealed": test_set_remains_sealed,
                "external_model_called": False,
                "json_report": str(json_path.relative_to(ROOT)),
                "markdown_report": str(markdown_path.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
