"""Phase 8D governed immutable dataset release."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.redaction import redact_text
from security_diagnosis_harness.evaluation.annotation import (
    AdjudicationDecision,
    AnnotationTask,
    BlindAnnotation,
    DatasetAdmissionCandidate,
    adjudicate_annotations,
)
from security_diagnosis_harness.evaluation.dataset import (
    DatasetCase,
    DatasetManifest,
    DatasetProtocolError,
    DatasetRegistry,
    DatasetSplit,
    ManifestFile,
    are_near_duplicate_cases,
)


class DatasetReleaseError(DatasetProtocolError):
    """A candidate or target violates the governed release protocol."""


class SourceKind(StrEnum):
    SYNTHETIC = "synthetic"
    AUTHORIZED_EXPORT = "authorized_export"


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SourceKind
    source_record_id: str = Field(min_length=1)
    authorization_reference: str = Field(min_length=1)
    authorized_by: str = Field(min_length=1)
    reviewed_at: datetime

    @model_validator(mode="after")
    def _reject_sensitive_metadata(self) -> SourceProvenance:
        for name in ("source_record_id", "authorization_reference", "authorized_by"):
            value = getattr(self, name)
            redacted, changed = redact_text(value)
            if changed or redacted != value:
                raise ValueError(f"{name} 不得包含敏感信息")
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at 必须包含时区")
        return self


class DatasetReleaseAddition(BaseModel):
    """Complete admission proof; no single prebuilt candidate is trusted alone."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposed_case: DatasetCase
    task: AnnotationTask
    first_annotation: BlindAnnotation
    second_annotation: BlindAnnotation
    decision: AdjudicationDecision
    admission_candidate: DatasetAdmissionCandidate
    provenance: SourceProvenance


class ReleasedAddition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    split: DatasetSplit
    source_kind: SourceKind
    source_record_id: str
    authorization_reference: str
    reviewed_at: datetime
    candidate_id: str
    annotation_ids: tuple[str, str]
    adjudication_id: str


class DatasetReleaseReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_name: str
    source_version: str
    released_version: str
    released_at: datetime
    release_kind: str = "governed_dataset_release"
    synthetic_case_count: int = Field(ge=0)
    authorized_case_count: int = Field(ge=0)
    total_case_count: int = Field(ge=1)
    split_counts: dict[DatasetSplit, int]
    additions: tuple[ReleasedAddition, ...]

    def to_markdown(self) -> str:
        lines = [
            "# Phase 8D 数据集发布回执", "",
            f"- Dataset: `{self.dataset_name}`",
            f"- Version: `{self.source_version}` → `{self.released_version}`",
            f"- 总案例数: `{self.total_case_count}`",
            f"- 合成新增: `{self.synthetic_case_count}`",
            f"- 授权新增: `{self.authorized_case_count}`", "", "## 新增案例", "",
            "| Case | Split | Source kind | Candidate | Adjudication |",
            "|---|---|---|---|---|",
        ]
        lines.extend(
            f"| `{item.case_id}` | `{item.split.value}` | `{item.source_kind.value}` | "
            f"`{item.candidate_id}` | `{item.adjudication_id}` |"
            for item in self.additions
        )
        lines.extend([
            "", "> 本回执只证明数据治理协议，不代表真实业务覆盖率或生产准确率。", "",
        ])
        return "\n".join(lines)


def publish_dataset_version(
    source_directory: Path,
    dataset_root: Path,
    target_version: str,
    additions: tuple[DatasetReleaseAddition, ...],
    *,
    released_at: datetime,
) -> tuple[Path, DatasetReleaseReceipt]:
    """Publish a new immutable semantic version after full governance revalidation."""
    _validate_semver(target_version)
    if not additions:
        raise DatasetReleaseError("新数据集版本至少需要一个治理后新增案例")
    if released_at.tzinfo is None or released_at.utcoffset() is None:
        raise DatasetReleaseError("released_at 必须包含时区")
    source_directory = source_directory.resolve()
    dataset_root = dataset_root.resolve()
    source_version = source_directory.name
    _validate_semver(source_version)
    if _semver(target_version) <= _semver(source_version):
        raise DatasetReleaseError("目标数据集版本必须高于来源版本")
    target = dataset_root / target_version
    if target.exists():
        raise DatasetReleaseError("目标数据集版本已存在且不可变")

    registry = DatasetRegistry.load(source_directory)
    existing = tuple(
        case for split in DatasetSplit for case in registry.cases(split, allow_test=True)
    )
    validated = tuple(_validate_addition(item, target_version) for item in additions)
    _validate_unique_and_not_duplicate(existing, validated)

    cases_by_split = {
        split: tuple(
            case.model_copy(update={"dataset_version": target_version})
            for case in registry.cases(split, allow_test=True)
        )
        for split in DatasetSplit
    }
    for case, _ in validated:
        cases_by_split[case.split] = (*cases_by_split[case.split], case)
    # Re-run the complete physical isolation protocol before writing anything.
    DatasetRegistry(cases_by_split)

    receipt = _receipt(source_version, target_version, cases_by_split, validated, released_at)
    dataset_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{target_version}-", dir=dataset_root) as temporary:
        staging = Path(temporary) / target_version
        staging.mkdir()
        for split, cases in cases_by_split.items():
            _write_split(staging / split.value, target_version, split, cases)
        _write_json(staging / "release.json", receipt.model_dump(mode="json"))
        # Verify staging fully before the immutable target becomes visible.
        DatasetRegistry.load(staging)
        try:
            staging.rename(target)
        except FileExistsError as exc:
            raise DatasetReleaseError("目标数据集版本已存在且不可变") from exc
    return target, receipt


def _validate_addition(
    addition: DatasetReleaseAddition, target_version: str
) -> tuple[DatasetCase, DatasetReleaseAddition]:
    try:
        item = DatasetReleaseAddition.model_validate(addition.model_dump(mode="python"))
        case = DatasetCase.model_validate(item.proposed_case.model_dump(mode="python"))
        recomputed = adjudicate_annotations(
            item.task, item.first_annotation, item.second_annotation, item.decision
        )
    except ValueError as exc:
        raise DatasetReleaseError("数据集准入证明未通过协议复验") from exc
    candidate = item.admission_candidate
    if candidate.status != "candidate":
        raise DatasetReleaseError("准入候选状态必须为 candidate")
    governed_fields = (
        "task_id", "source_case_id", "fault_type", "proposed_candidate_label",
        "proposed_expected_tools", "proposed_required_evidence_types", "annotation_ids",
        "adjudication_id", "adjudicator", "rationale", "status",
    )
    if any(getattr(candidate, name) != getattr(recomputed, name) for name in governed_fields):
        raise DatasetReleaseError("准入候选与盲标裁决重算结果不一致")
    if case.case_id != candidate.source_case_id or case.case_id != item.task.case_id:
        raise DatasetReleaseError("案例、盲标任务与准入候选身份不一致")
    if case.fault_type is not candidate.fault_type or case.input_facts != item.task.input_facts:
        raise DatasetReleaseError("案例事实或故障域与盲标任务不一致")
    if case.allowed_tools != item.task.allowed_tools:
        raise DatasetReleaseError("案例允许工具与盲标任务不一致")
    if (
        case.expected_candidate != candidate.proposed_candidate_label
        or case.expected_tools != candidate.proposed_expected_tools
        or case.required_evidence_types != candidate.proposed_required_evidence_types
    ):
        raise DatasetReleaseError("案例标准答案与裁决结果不一致")
    if case.dataset_version != target_version:
        raise DatasetReleaseError("新增案例必须声明目标数据集版本")
    if case.source_record_id != item.provenance.source_record_id:
        raise DatasetReleaseError("案例来源记录与授权谱系不一致")
    if item.provenance.kind is SourceKind.SYNTHETIC and not case.source.startswith("synthetic"):
        raise DatasetReleaseError("合成来源必须明确标记为 synthetic")
    if item.provenance.kind is SourceKind.AUTHORIZED_EXPORT and case.source.startswith("synthetic"):
        raise DatasetReleaseError("授权导出来源不得伪装为 synthetic")
    return case, item


def _validate_unique_and_not_duplicate(
    existing: tuple[DatasetCase, ...],
    validated: tuple[tuple[DatasetCase, DatasetReleaseAddition], ...],
) -> None:
    accepted: list[DatasetCase] = list(existing)
    identities = {case.case_id for case in existing}
    templates = {case.template_group_id for case in existing}
    sources = {(case.source, case.source_record_id) for case in existing}
    candidate_ids: set[str] = set()
    annotation_ids: set[str] = set()
    adjudication_ids: set[str] = set()
    for case, item in validated:
        if case.case_id in identities or case.template_group_id in templates:
            raise DatasetReleaseError("新增案例身份或模板组已存在")
        if (case.source, case.source_record_id) in sources:
            raise DatasetReleaseError("新增案例来源记录已存在")
        candidate = item.admission_candidate
        if candidate.candidate_id in candidate_ids:
            raise DatasetReleaseError("同批新增案例不得复用 candidate_id")
        if set(candidate.annotation_ids) & annotation_ids:
            raise DatasetReleaseError("同批新增案例不得复用 annotation_id")
        if candidate.adjudication_id in adjudication_ids:
            raise DatasetReleaseError("同批新增案例不得复用 adjudication_id")
        if any(
            case.fault_type is other.fault_type
            and (
                case.structural_fingerprint() == other.structural_fingerprint()
                or are_near_duplicate_cases(case, other)
            )
            for other in accepted
        ):
            raise DatasetReleaseError("新增案例与既有或同批案例近重复")
        identities.add(case.case_id)
        templates.add(case.template_group_id)
        sources.add((case.source, case.source_record_id))
        candidate_ids.add(candidate.candidate_id)
        annotation_ids.update(candidate.annotation_ids)
        adjudication_ids.add(candidate.adjudication_id)
        accepted.append(case)


def _write_split(
    directory: Path,
    version: str,
    split: DatasetSplit,
    cases: tuple[DatasetCase, ...],
) -> None:
    directory.mkdir()
    files: list[ManifestFile] = []
    for case in sorted(cases, key=lambda item: item.case_id):
        filename = f"{case.case_id}.json"
        payload = case.model_dump(mode="json")
        _write_json(directory / filename, payload)
        files.append(ManifestFile(path=filename, sha256=sha256_text(canonical_json(payload))))
    manifest = DatasetManifest(
        dataset_name="security-diagnosis",
        dataset_version=version,
        split=split,
        description=f"Phase 8D governed {split.value} split",
        files=tuple(files),
    )
    _write_json(directory / "manifest.json", manifest.model_dump(mode="json"))


def _receipt(
    source_version: str,
    target_version: str,
    cases: dict[DatasetSplit, tuple[DatasetCase, ...]],
    validated: tuple[tuple[DatasetCase, DatasetReleaseAddition], ...],
    released_at: datetime,
) -> DatasetReleaseReceipt:
    additions = tuple(
        ReleasedAddition(
            case_id=case.case_id,
            split=case.split,
            source_kind=item.provenance.kind,
            source_record_id=case.source_record_id,
            authorization_reference=item.provenance.authorization_reference,
            reviewed_at=item.provenance.reviewed_at,
            candidate_id=item.admission_candidate.candidate_id,
            annotation_ids=item.admission_candidate.annotation_ids,
            adjudication_id=item.admission_candidate.adjudication_id,
        )
        for case, item in validated
    )
    return DatasetReleaseReceipt(
        dataset_name="security-diagnosis",
        source_version=source_version,
        released_version=target_version,
        released_at=released_at,
        synthetic_case_count=sum(item.source_kind is SourceKind.SYNTHETIC for item in additions),
        authorized_case_count=sum(
            item.source_kind is SourceKind.AUTHORIZED_EXPORT for item in additions
        ),
        total_case_count=sum(len(items) for items in cases.values()),
        split_counts={split: len(items) for split, items in cases.items()},
        additions=additions,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_dataset_release_report(
    receipt: DatasetReleaseReceipt, output_directory: Path
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "phase8-dataset-release.json"
    markdown_path = output_directory / "phase8-dataset-release.md"
    _atomic_write(json_path, receipt.model_dump_json(indent=2))
    _atomic_write(markdown_path, receipt.to_markdown())
    return json_path, markdown_path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _validate_semver(value: str) -> None:
    try:
        _semver(value)
    except (TypeError, ValueError) as exc:
        raise DatasetReleaseError("数据集版本必须是三段数字语义版本") from exc


def _semver(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or any(not item.isdigit() for item in parts):
        raise ValueError(value)
    return tuple(int(item) for item in parts)  # type: ignore[return-value]
