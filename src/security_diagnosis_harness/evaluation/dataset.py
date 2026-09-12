"""Phase 7A 数据集协议、加载校验与跨集合泄漏检测。"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.domain.redaction import redact_mapping


class DatasetProtocolError(ValueError):
    """数据集文件、清单或案例违反协议。"""


class DatasetLeakageError(DatasetProtocolError):
    """不同数据集合之间存在同源或近重复泄漏。"""


class TestSetAccessError(DatasetProtocolError):
    """非发布门禁流程尝试读取密封 Test Set。"""


class DatasetSplit(StrEnum):
    DEV = "dev"
    VALIDATION = "validation"
    TEST = "test"


class ForbiddenBehavior(StrEnum):
    AUTO_CONFIRM = "auto_confirm"
    CROSS_FAULT_CONCLUSION = "cross_fault_conclusion"
    UNCITED_CONCLUSION = "uncited_conclusion"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    SENSITIVE_DATA_LEAK = "sensitive_data_leak"
    UNBOUNDED_RETRY = "unbounded_retry"


class EvaluationBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_rounds: int = Field(ge=1, le=20)
    max_tool_calls: int = Field(ge=0, le=50)
    timeout_seconds: int = Field(ge=1, le=300)
    max_model_calls: int = Field(default=0, ge=0, le=10)
    max_prompt_tokens: int = Field(default=2000, ge=1, le=100_000)
    max_completion_tokens: int = Field(default=500, ge=1, le=10_000)
    max_estimated_cost: float = Field(default=0.05, ge=0, le=100)


class DatasetCase(BaseModel):
    """一个可独立评分、具有来源谱系的评测案例。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version: str = Field(min_length=1)
    split: DatasetSplit
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    template_group_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    source: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    fault_type: SecurityFaultType
    input_facts: dict[str, Any]
    allowed_tools: tuple[str, ...]
    expected_tools: tuple[str, ...]
    expected_candidate: str = Field(min_length=1)
    required_evidence_types: tuple[EvidenceType, ...]
    forbidden_behaviors: tuple[ForbiddenBehavior, ...]
    budget: EvaluationBudget

    @model_validator(mode="after")
    def _validate_case_contract(self) -> DatasetCase:
        if not self.input_facts:
            raise ValueError("input_facts 不能为空")
        if not self.allowed_tools:
            raise ValueError("allowed_tools 不能为空")
        if not self.expected_tools:
            raise ValueError("expected_tools 不能为空")
        if not set(self.expected_tools).issubset(self.allowed_tools):
            raise ValueError("expected_tools 必须是 allowed_tools 的子集")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed_tools 不允许重复")
        if not self.required_evidence_types:
            raise ValueError("required_evidence_types 不能为空")
        if not self.forbidden_behaviors:
            raise ValueError("forbidden_behaviors 不能为空")
        redacted, _ = redact_mapping(self.input_facts)
        if redacted != self.input_facts:
            raise ValueError("input_facts 含未脱敏的敏感信息")
        return self

    def structural_fingerprint(self) -> str:
        """忽略实例标识后计算事实结构指纹，用于捕获复制改名。"""
        return sha256_text(canonical_json(_strip_instance_identifiers(self.input_facts)))

    def similarity_text(self) -> str:
        return canonical_json(_strip_instance_identifiers(self.input_facts)).lower()


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=r"^[^/\\]+\.json$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    split: DatasetSplit
    description: str = Field(min_length=1)
    files: tuple[ManifestFile, ...]

    @model_validator(mode="after")
    def _files_are_unique(self) -> DatasetManifest:
        paths = [item.path for item in self.files]
        if not paths or len(paths) != len(set(paths)):
            raise ValueError("manifest files 必须非空且唯一")
        return self


def load_dataset_split(directory: Path) -> tuple[DatasetManifest, tuple[DatasetCase, ...]]:
    """按 manifest 加载一个物理 split，并验证路径、hash 与案例元数据。"""
    root = directory.resolve()
    manifest_path = root / "manifest.json"
    try:
        manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DatasetProtocolError(f"无法读取合法数据集清单: {manifest_path.name}") from exc

    if root.name != manifest.split.value:
        raise DatasetProtocolError("manifest split 与物理目录不一致")

    cases: list[DatasetCase] = []
    for entry in manifest.files:
        case_path = (root / entry.path).resolve()
        if case_path.parent != root:
            raise DatasetProtocolError("案例路径不得逃逸 split 目录")
        try:
            raw = case_path.read_bytes()
        except OSError as exc:
            raise DatasetProtocolError(f"案例文件不存在: {entry.path}") from exc
        if _json_content_hash(raw) != entry.sha256:
            raise DatasetProtocolError(f"案例文件 hash 不匹配: {entry.path}")
        try:
            case = DatasetCase.model_validate_json(raw)
        except ValueError as exc:
            raise DatasetProtocolError(f"案例协议不合法: {entry.path}") from exc
        if case.split is not manifest.split or case.dataset_version != manifest.dataset_version:
            raise DatasetProtocolError(f"案例 split/version 与清单不一致: {entry.path}")
        cases.append(case)

    if len({case.case_id for case in cases}) != len(cases):
        raise DatasetProtocolError("同一 split 中 case_id 不允许重复")
    return manifest, tuple(cases)


class DatasetRegistry:
    """汇总三个 split，并在评测执行前阻断数据泄漏。"""

    def __init__(self, datasets: dict[DatasetSplit, tuple[DatasetCase, ...]]) -> None:
        if set(datasets) != set(DatasetSplit):
            raise DatasetProtocolError("必须同时提供 dev、validation、test")
        self._datasets = {split: tuple(cases) for split, cases in datasets.items()}
        self._validate_cross_split_isolation()

    @classmethod
    def load(cls, version_directory: Path) -> DatasetRegistry:
        loaded: dict[DatasetSplit, tuple[DatasetCase, ...]] = {}
        versions: set[str] = set()
        for split in DatasetSplit:
            manifest, cases = load_dataset_split(version_directory / split.value)
            loaded[split] = cases
            versions.add(manifest.dataset_version)
        if len(versions) != 1:
            raise DatasetProtocolError("三个 split 的 dataset_version 必须一致")
        return cls(loaded)

    def cases(
        self, split: DatasetSplit, *, allow_test: bool = False
    ) -> tuple[DatasetCase, ...]:
        if split is DatasetSplit.TEST and not allow_test:
            raise TestSetAccessError("Test Set 仅允许发布门禁显式读取")
        return self._datasets[split]

    def case_count(self, split: DatasetSplit) -> int:
        """返回数量而不暴露 Test Set 案例和标签。"""
        return len(self._datasets[split])

    def _validate_cross_split_isolation(self) -> None:
        seen_case: dict[str, DatasetSplit] = {}
        seen_template: dict[str, DatasetSplit] = {}
        seen_source: dict[tuple[str, str], DatasetSplit] = {}
        seen_fingerprint: dict[Any, DatasetSplit] = {}
        prior: list[tuple[DatasetSplit, DatasetCase]] = []

        for split, cases in self._datasets.items():
            for case in cases:
                _assert_not_cross_split(seen_case, case.case_id, split, "case_id")
                _assert_not_cross_split(
                    seen_template, case.template_group_id, split, "template_group_id"
                )
                _assert_not_cross_split(
                    seen_source, (case.source, case.source_record_id), split, "source_record"
                )
                _assert_not_cross_split(
                    seen_fingerprint,
                    (case.fault_type, case.structural_fingerprint()),
                    split,
                    "故障域内事实结构指纹",
                )
                for other_split, other in prior:
                    if other_split is not split and _near_duplicate(case, other):
                        raise DatasetLeakageError(
                            f"近重复案例跨集合: {other.case_id} / {case.case_id}"
                        )
                prior.append((split, case))


def _assert_not_cross_split(
    seen: dict[Any, DatasetSplit], key: Any, split: DatasetSplit, label: str
) -> None:
    prior = seen.setdefault(key, split)
    if prior is not split:
        raise DatasetLeakageError(f"{label} 跨集合复用: {key}")


_INSTANCE_KEY = re.compile(r"(?:^|_)(?:device|channel|event|alarm|record|case)_?id$")


def _strip_instance_identifiers(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_instance_identifiers(item)
            for key, item in sorted(value.items())
            if not _INSTANCE_KEY.search(key.lower())
        }
    if isinstance(value, list):
        return [_strip_instance_identifiers(item) for item in value]
    return value


def _near_duplicate(left: DatasetCase, right: DatasetCase) -> bool:
    if left.fault_type is not right.fault_type:
        return False
    left_grams = _character_ngrams(left.similarity_text())
    right_grams = _character_ngrams(right.similarity_text())
    if not left_grams or not right_grams:
        return False
    return len(left_grams & right_grams) / len(left_grams | right_grams) >= 0.92


def _character_ngrams(text: str, size: int = 3) -> set[str]:
    normalized = re.sub(r"\s+", "", text)
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def _json_content_hash(raw: bytes) -> str:
    """对 JSON 语义内容计算跨平台稳定 hash，忽略缩进和换行差异。"""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetProtocolError("案例文件不是合法 UTF-8 JSON") from exc
    return sha256_text(canonical_json(payload))
