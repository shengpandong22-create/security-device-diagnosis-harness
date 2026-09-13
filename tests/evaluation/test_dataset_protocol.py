from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.domain.common import sha256_text
from security_diagnosis_harness.evaluation import (
    DatasetCase,
    DatasetLeakageError,
    DatasetProtocolError,
    DatasetRegistry,
    DatasetSplit,
    load_dataset_split,
)
from security_diagnosis_harness.evaluation import (
    TestSetAccessError as SealedTestSetAccessError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = REPO_ROOT / "datasets" / "security-diagnosis" / "1.0.0"


@pytest.fixture(scope="module")
def registry() -> DatasetRegistry:
    return DatasetRegistry.load(DATASET_ROOT)


def test_all_three_splits_are_physically_separated(registry: DatasetRegistry):
    for split in DatasetSplit:
        assert (DATASET_ROOT / split.value / "manifest.json").is_file()
        assert registry.case_count(split) > 0


def test_cases_contain_complete_evaluation_contract(registry: DatasetRegistry):
    for split in DatasetSplit:
        for case in registry.cases(split, allow_test=True):
            assert case.dataset_version == "1.0.0"
            assert case.input_facts
            assert case.allowed_tools
            assert case.expected_tools
            assert set(case.expected_tools).issubset(case.allowed_tools)
            assert case.expected_candidate
            assert case.required_evidence_types
            assert case.forbidden_behaviors
            assert case.budget.timeout_seconds > 0
            assert case.budget.max_prompt_tokens > 0
            assert case.budget.max_completion_tokens > 0
            assert case.budget.max_estimated_cost >= 0


def test_case_and_lineage_ids_are_unique_across_splits(registry: DatasetRegistry):
    cases = [
        case for split in DatasetSplit for case in registry.cases(split, allow_test=True)
    ]
    assert len({case.case_id for case in cases}) == len(cases)
    assert len({case.template_group_id for case in cases}) == len(cases)
    assert len({(case.source, case.source_record_id) for case in cases}) == len(cases)


def test_manifest_hashes_are_verified(tmp_path: Path):
    target = tmp_path / "dev"
    shutil.copytree(DATASET_ROOT / "dev", target)
    case_path = target / "camera_stream_timeout.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    payload["expected_candidate"] = "tampered_candidate"
    case_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DatasetProtocolError, match="hash 不匹配"):
        load_dataset_split(target)


def test_manifest_split_must_match_physical_directory(tmp_path: Path):
    target = tmp_path / "wrong-name"
    shutil.copytree(DATASET_ROOT / "dev", target)
    with pytest.raises(DatasetProtocolError, match="物理目录"):
        load_dataset_split(target)


def test_version_directory_name_must_match_manifest_version(tmp_path: Path):
    target = tmp_path / "9.9.9"
    shutil.copytree(DATASET_ROOT, target)
    with pytest.raises(DatasetProtocolError, match="目录名"):
        DatasetRegistry.load(target)


def test_all_split_manifests_must_share_dataset_name(tmp_path: Path):
    target = tmp_path / "1.0.0"
    shutil.copytree(DATASET_ROOT, target)
    manifest_path = target / "validation/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_name"] = "another-dataset"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DatasetProtocolError, match="dataset_name"):
        DatasetRegistry.load(target)


def test_case_split_must_match_manifest(tmp_path: Path):
    target = tmp_path / "dev"
    shutil.copytree(DATASET_ROOT / "dev", target)
    case_path = target / "camera_stream_timeout.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    payload["split"] = "test"
    _write_case_and_refresh_hash(target, "camera_stream_timeout.json", payload)
    with pytest.raises(DatasetProtocolError, match="split/version"):
        load_dataset_split(target)


def test_sensitive_input_facts_are_rejected(registry: DatasetRegistry):
    payload = registry.cases(DatasetSplit.DEV)[0].model_dump(mode="json")
    payload["input_facts"]["password"] = "plain-secret-value"
    with pytest.raises(ValidationError, match="未脱敏"):
        DatasetCase.model_validate(payload)


def test_test_set_requires_explicit_release_gate_access(registry: DatasetRegistry):
    with pytest.raises(SealedTestSetAccessError, match="发布门禁"):
        registry.cases(DatasetSplit.TEST)
    assert registry.cases(DatasetSplit.TEST, allow_test=True)


@pytest.mark.parametrize("field", ["case_id", "template_group_id"])
def test_duplicate_identity_across_splits_is_rejected(
    registry: DatasetRegistry, field: str
):
    datasets = _dataset_copy(registry)
    dev = datasets[DatasetSplit.DEV][0]
    validation = datasets[DatasetSplit.VALIDATION][0]
    changed = validation.model_copy(update={field: getattr(dev, field)})
    datasets[DatasetSplit.VALIDATION] = (changed, *datasets[DatasetSplit.VALIDATION][1:])
    with pytest.raises(DatasetLeakageError, match=field):
        DatasetRegistry(datasets)


def test_duplicate_source_record_across_splits_is_rejected(registry: DatasetRegistry):
    datasets = _dataset_copy(registry)
    dev = datasets[DatasetSplit.DEV][0]
    validation = datasets[DatasetSplit.VALIDATION][0]
    changed = validation.model_copy(
        update={"source": dev.source, "source_record_id": dev.source_record_id}
    )
    datasets[DatasetSplit.VALIDATION] = (changed, *datasets[DatasetSplit.VALIDATION][1:])
    with pytest.raises(DatasetLeakageError, match="source_record"):
        DatasetRegistry(datasets)


def test_copy_renamed_case_is_rejected_by_structural_fingerprint(registry: DatasetRegistry):
    datasets = _dataset_copy(registry)
    dev = datasets[DatasetSplit.DEV][0]
    validation = datasets[DatasetSplit.VALIDATION][0]
    changed = validation.model_copy(
        update={
            "fault_type": dev.fault_type,
            "input_facts": {**dev.input_facts, "device_id": "renamed-device"},
        }
    )
    datasets[DatasetSplit.VALIDATION] = (changed, *datasets[DatasetSplit.VALIDATION][1:])
    with pytest.raises(DatasetLeakageError, match="事实结构指纹"):
        DatasetRegistry(datasets)


def test_near_duplicate_across_splits_is_rejected(registry: DatasetRegistry):
    datasets = _dataset_copy(registry)
    dev = datasets[DatasetSplit.DEV][0]
    validation = datasets[DatasetSplit.VALIDATION][0]
    facts = deepcopy(dev.input_facts)
    facts["bitrate_kbps"] = 8193
    changed = validation.model_copy(update={"fault_type": dev.fault_type, "input_facts": facts})
    datasets[DatasetSplit.VALIDATION] = (changed, *datasets[DatasetSplit.VALIDATION][1:])
    with pytest.raises(DatasetLeakageError, match="近重复"):
        DatasetRegistry(datasets)


def test_different_fault_domains_are_not_near_duplicates(registry: DatasetRegistry):
    datasets = _dataset_copy(registry)
    dev = datasets[DatasetSplit.DEV][0]
    validation = datasets[DatasetSplit.VALIDATION][0]
    changed = validation.model_copy(update={"input_facts": deepcopy(dev.input_facts)})
    datasets[DatasetSplit.VALIDATION] = (changed, *datasets[DatasetSplit.VALIDATION][1:])
    DatasetRegistry(datasets)


def _dataset_copy(registry: DatasetRegistry) -> dict[DatasetSplit, tuple[DatasetCase, ...]]:
    return {split: tuple(registry.cases(split, allow_test=True)) for split in DatasetSplit}


def _write_case_and_refresh_hash(directory: Path, name: str, payload: dict) -> None:
    case_path = directory / name
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    case_path.write_bytes(text.encode("utf-8"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == name:
            entry["sha256"] = sha256_text(
                json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
