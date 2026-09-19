"""手工知识种子的来源完整性、职责分离与召回验收。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.application.knowledge_seeds import (
    load_manual_knowledge_seeds,
)
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.errors import KnowledgeReviewNotAllowed
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidateSource,
    KnowledgeCandidateStatus,
    KnowledgeReviewAction,
)
from security_diagnosis_harness.runtime import build_runtime_container
from security_diagnosis_harness.tools.contracts import ToolExecutionContext, ToolPermission

SEEDS_PATH = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "knowledge"
    / "default_sops.manual-seeds.json"
)


def test_default_sop_manifest_has_four_digest_bound_manual_seeds():
    seeds = load_manual_knowledge_seeds(SEEDS_PATH)

    assert len(seeds) == 4
    assert len({seed.artifact_id for seed in seeds}) == 4
    assert all(seed.artifact_sha256 == seed.content_sha256() for seed in seeds)


def test_tampered_manual_seed_manifest_is_rejected(tmp_path: Path):
    data = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    data[0]["summary"] = "被篡改的内容"
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="sha256 mismatch"):
        load_manual_knowledge_seeds(tampered)


def test_manual_seed_requires_independent_review_before_runtime_recall():
    seed = load_manual_knowledge_seeds(SEEDS_PATH)[0]
    with build_runtime_container(RuntimeSettings(repository_mode="memory")) as runtime:
        candidate = runtime.knowledge_service.import_manual_seed(seed, "seed-operator")
        context = ToolExecutionContext(
            diagnosis_id="diag-current",
            fault_type=seed.fault_type,
            permissions=frozenset({ToolPermission.KNOWLEDGE_READ}),
        )

        before = runtime.registry.execute(
            "knowledge__search", {"query": seed.title, "limit": 3}, context
        )
        with pytest.raises(KnowledgeReviewNotAllowed, match="不能审核自己"):
            runtime.knowledge_service.review(
                candidate.knowledge_id,
                KnowledgeReviewAction.CONFIRM,
                "seed-operator",
            )
        confirmed = runtime.knowledge_service.review(
            candidate.knowledge_id,
            KnowledgeReviewAction.CONFIRM,
            "independent-reviewer",
        )
        after = runtime.registry.execute(
            "knowledge__search", {"query": seed.title, "limit": 3}, context
        )

        assert candidate.source is KnowledgeCandidateSource.MANUAL_SEED
        assert candidate.status is KnowledgeCandidateStatus.CANDIDATE
        assert candidate.source_diagnosis_id is None
        assert candidate.source_artifact_id == seed.artifact_id
        assert before.evidence_drafts[0].payload["sops"] == []
        assert confirmed.status is KnowledgeCandidateStatus.CONFIRMED
        assert after.evidence_drafts[0].payload["sops"][0]["sop_id"] == candidate.knowledge_id

        runtime.knowledge_service.review(
            candidate.knowledge_id,
            KnowledgeReviewAction.RETIRE,
            "retirement-reviewer",
        )
        retired = runtime.registry.execute(
            "knowledge__search", {"query": seed.title, "limit": 3}, context
        )
        assert retired.evidence_drafts[0].payload["sops"] == []


def test_manual_seed_import_and_review_are_audited():
    seed = load_manual_knowledge_seeds(SEEDS_PATH)[0]
    with build_runtime_container(RuntimeSettings(repository_mode="memory")) as runtime:
        candidate = runtime.knowledge_service.import_manual_seed(seed, "seed-operator")
        runtime.knowledge_service.review(
            candidate.knowledge_id,
            KnowledgeReviewAction.CONFIRM,
            "independent-reviewer",
        )

        actions = [
            event.action
            for event in runtime.audit_repository.list_for_entity(candidate.knowledge_id)
        ]
        assert actions == [
            "knowledge.manual_seed.imported",
            "knowledge.review.confirm",
        ]
