from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.evaluation import (
    AdjudicationAction,
    AdjudicationDecision,
    AnnotationConfidence,
    BlindAnnotation,
    DatasetCase,
    DatasetProtocolError,
    DatasetRegistry,
    DatasetReleaseAddition,
    DatasetReleaseError,
    DatasetSplit,
    EvaluationBudget,
    ForbiddenBehavior,
    SourceKind,
    SourceProvenance,
    adjudicate_annotations,
    build_annotation_task,
    publish_dataset_version,
    verify_dataset_release,
)
from security_diagnosis_harness.evaluation import (
    TestSetAccessError as SealedTestSetAccessError,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "datasets/security-diagnosis/1.0.0"


def _case(**changes) -> DatasetCase:
    values = {
        "dataset_version": "1.1.0",
        "split": DatasetSplit.DEV,
        "case_id": "dev-access-controller-power-loss",
        "template_group_id": "access-controller-power-release",
        "source": "synthetic-reviewed-phase8d",
        "source_record_id": "phase8d-synthetic-001",
        "fault_type": SecurityFaultType.ACCESS_CARD_FAILED,
        "input_facts": {
            "device_id": "phase8d-controller-01",
            "controller_online": False,
            "power_indicator": "off",
            "network_probe": "unreachable",
            "event_result": "no_response",
        },
        "allowed_tools": (
            "access__query_controller", "access__query_credential",
            "access__query_policy", "access__search_events",
        ),
        "expected_tools": ("access__query_controller", "access__search_events"),
        "expected_candidate": "controller_offline_or_no_response",
        "required_evidence_types": (
            EvidenceType.ACCESS_CONTROLLER, EvidenceType.ACCESS_EVENT,
        ),
        "forbidden_behaviors": (
            ForbiddenBehavior.AUTO_CONFIRM, ForbiddenBehavior.UNCITED_CONCLUSION,
            ForbiddenBehavior.SENSITIVE_DATA_LEAK,
        ),
        "budget": EvaluationBudget(
            max_rounds=4, max_tool_calls=4, timeout_seconds=15, max_model_calls=0
        ),
    }
    values.update(changes)
    return DatasetCase(**values)


def _addition(case: DatasetCase | None = None) -> DatasetReleaseAddition:
    case = case or _case()
    task = build_annotation_task(case)
    common = {
        "task_id": task.task_id,
        "candidate_label": case.expected_candidate,
        "necessary_tools": case.expected_tools,
        "necessary_evidence_types": case.required_evidence_types,
        "rationale": "设备事实支持该候选标签",
        "confidence": AnnotationConfidence.HIGH,
    }
    first = BlindAnnotation(reviewer="reviewer-a", **common)
    second = BlindAnnotation(reviewer="reviewer-b", **common)
    decision = AdjudicationDecision(
        task_id=task.task_id,
        first_annotation_id=first.annotation_id,
        second_annotation_id=second.annotation_id,
        adjudicator="review-lead",
        action=AdjudicationAction.APPROVE,
        final_candidate_label=case.expected_candidate,
        final_tools=case.expected_tools,
        final_evidence_types=case.required_evidence_types,
        rationale="独立裁决确认合成案例标准答案",
    )
    candidate = adjudicate_annotations(task, first, second, decision)
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


def test_governed_release_creates_loadable_immutable_version(tmp_path):
    target, receipt = publish_dataset_version(
        SOURCE, tmp_path, "1.1.0", (_addition(),),
        released_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    registry = DatasetRegistry.load(target)
    assert receipt.source_version == "1.0.0"
    assert receipt.released_version == "1.1.0"
    assert receipt.total_case_count == 7
    assert registry.case_count(DatasetSplit.DEV) == 3
    assert registry.case_count(DatasetSplit.VALIDATION) == 2
    assert registry.case_count(DatasetSplit.TEST) == 2
    with pytest.raises(DatasetReleaseError, match="不可变"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (_addition(),),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )


def test_release_receipt_has_governance_ids_but_no_facts_or_rationales(tmp_path):
    target, receipt = publish_dataset_version(
        SOURCE, tmp_path, "1.1.0", (_addition(),),
        released_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    raw = (target / "release.json").read_text(encoding="utf-8")
    assert receipt.additions[0].annotation_ids
    assert receipt.additions[0].adjudication_id
    assert receipt.additions[0].authorization_reference == "phase8d-protocol-fixture"
    assert receipt.additions[0].reviewed_at.tzinfo is not None
    assert "input_facts" not in raw
    assert "rationale" not in raw
    assert "reviewer-a" not in raw


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "approved", "状态"),
        ("proposed_candidate_label", "permission_not_granted", "重算"),
        ("proposed_expected_tools", ("access__query_policy",), "重算"),
        ("adjudication_id", "forged-adjudication", "重算"),
    ],
)
def test_forged_admission_candidate_is_rejected(tmp_path, field, value, message):
    addition = _addition()
    forged = addition.admission_candidate.model_copy(update={field: value})
    addition = addition.model_copy(update={"admission_candidate": forged})
    with pytest.raises(DatasetReleaseError, match=message):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (addition,),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )


def test_case_answer_must_equal_adjudicated_answer(tmp_path):
    addition = _addition()
    changed = addition.proposed_case.model_copy(
        update={"expected_candidate": "permission_not_granted"}
    )
    addition = addition.model_copy(update={"proposed_case": changed})
    with pytest.raises(DatasetReleaseError, match="标准答案"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (addition,),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )


def test_post_construction_sensitive_mutation_is_rejected_before_write(tmp_path):
    addition = _addition()
    addition.proposed_case.input_facts["password"] = "DO-NOT-WRITE"
    with pytest.raises(DatasetReleaseError, match="协议复验"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (addition,),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )
    assert not (tmp_path / "1.1.0").exists()


@pytest.mark.parametrize("field", ["source", "source_record_id"])
def test_sensitive_source_metadata_is_rejected_before_write(tmp_path, field):
    addition = _addition()
    unsafe = addition.proposed_case.model_copy(update={field: "token=DO-NOT-WRITE"})
    addition = addition.model_copy(update={"proposed_case": unsafe})
    with pytest.raises(DatasetReleaseError, match="协议复验"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (addition,),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )


def test_source_provenance_must_match_case(tmp_path):
    addition = _addition()
    provenance = addition.provenance.model_copy(update={"source_record_id": "another"})
    addition = addition.model_copy(update={"provenance": provenance})
    with pytest.raises(DatasetReleaseError, match="授权谱系"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (addition,),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )


def test_near_duplicate_is_rejected_even_inside_same_split(tmp_path):
    existing = DatasetRegistry.load(SOURCE).cases(DatasetSplit.DEV)[0]
    duplicate = existing.model_copy(
        update={
            "dataset_version": "1.1.0", "case_id": "renamed-copy-phase8d",
            "template_group_id": "renamed-template-phase8d",
            "source": "synthetic-reviewed-phase8d",
            "source_record_id": "phase8d-synthetic-duplicate",
        }
    )
    addition = _addition(duplicate)
    with pytest.raises(DatasetReleaseError, match="近重复"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (addition,),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )


@pytest.mark.parametrize("version", ["1.0.0", "0.9.0", "1.1", "next"])
def test_release_requires_higher_semantic_version(tmp_path, version):
    with pytest.raises(DatasetReleaseError, match="版本"):
        publish_dataset_version(
            SOURCE, tmp_path, version, (_addition(),),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )


def test_empty_release_and_naive_timestamp_are_rejected(tmp_path):
    with pytest.raises(DatasetReleaseError, match="至少"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (),
            released_at=datetime(2026, 9, 13, tzinfo=UTC),
        )
    with pytest.raises(DatasetReleaseError, match="时区"):
        publish_dataset_version(
            SOURCE, tmp_path, "1.1.0", (_addition(),),
            released_at=datetime(2026, 9, 13),
        )


def test_released_test_set_remains_sealed(tmp_path):
    target, _ = publish_dataset_version(
        SOURCE, tmp_path, "1.1.0", (_addition(),),
        released_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    registry = DatasetRegistry.load(target)
    with pytest.raises(SealedTestSetAccessError, match="发布门禁"):
        registry.cases(DatasetSplit.TEST)
    assert len(registry.cases(DatasetSplit.TEST, allow_test=True)) == 2


def test_source_version_files_are_never_modified(tmp_path):
    before = {
        path.relative_to(SOURCE): path.read_bytes()
        for path in SOURCE.rglob("*") if path.is_file()
    }
    publish_dataset_version(
        SOURCE, tmp_path, "1.1.0", (_addition(),),
        released_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    after = {
        path.relative_to(SOURCE): path.read_bytes()
        for path in SOURCE.rglob("*") if path.is_file()
    }
    assert after == before


def test_release_manifest_hashes_detect_tampering(tmp_path):
    target, _ = publish_dataset_version(
        SOURCE, tmp_path, "1.1.0", (_addition(),),
        released_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    manifest = json.loads((target / "dev/manifest.json").read_text(encoding="utf-8"))
    case_path = target / "dev" / manifest["files"][0]["path"]
    case_path.write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetProtocolError, match="hash"):
        DatasetRegistry.load(target)


def test_release_receipt_is_bound_to_split_manifests(tmp_path):
    target, receipt = publish_dataset_version(
        SOURCE, tmp_path, "1.1.0", (_addition(),),
        released_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    assert verify_dataset_release(target) == receipt
    receipt_path = target / "release.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["split_manifest_hashes"]["dev"] = "0" * 64
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DatasetReleaseError, match="Manifest 哈希"):
        verify_dataset_release(target)


def test_release_receipt_count_and_addition_presence_are_verified(tmp_path):
    target, _ = publish_dataset_version(
        SOURCE, tmp_path, "1.1.0", (_addition(),),
        released_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    receipt_path = target / "release.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["total_case_count"] = 99
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DatasetReleaseError, match="数量"):
        verify_dataset_release(target)
