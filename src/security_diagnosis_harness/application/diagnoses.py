"""SecurityDiagnosisApplicationService：把 Ports / Tools / Runner 串成闭环。

职责边界：

- 应用服务是唯一允许推进 `SecurityDiagnosisCase` 状态的地方；
- 它把 `ToolEvidenceDraft` 落成真实 `DiagnosisEvidence`；
- 它把模型 `ConclusionDraft` 落成 `DiagnosisConclusion` 并交给 `CitationPolicy` 校验；
- 它不绕过 CitationPolicy，也不允许模型产生 confirmed。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.agent.runner import (
    ToolLoopBudget,
    ToolLoopResult,
    ToolLoopRunner,
)
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.citation_policy import (
    DEVICE_FACT_EVIDENCE_TYPES,
    CitationPolicy,
)
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.errors import (
    CitationPolicyViolation,
    InvalidStatusTransition,
)
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction
from security_diagnosis_harness.ports.device_gateway import DeviceGateway
from security_diagnosis_harness.tools.contracts import ToolEvidenceDraft, ToolExecutionContext
from security_diagnosis_harness.tools.registry import ToolRegistry, default_permissions


class CitationRepair(BaseModel):
    """引用修正结果。"""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(default_factory=list)
    confidence: ConclusionConfidence
    repaired: bool = False
    downgraded: bool = False


class RunDiagnosisResult(BaseModel):
    """一次诊断运行的结果。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    ok: bool = True
    status: SecurityDiagnosisStatus
    evidence_count: int = 0
    conclusion: DiagnosisConclusion | None = None
    citations_repaired: bool = False
    confidence_downgraded: bool = False
    rounds: int = 0
    tool_calls: int = 0
    error: str | None = None


class ReviewResult(BaseModel):
    """一次人工审核的结果。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    ok: bool = True
    status: SecurityDiagnosisStatus
    action: HumanReviewAction
    review: HumanReview


# 允许启动一次诊断运行的状态。
RUNNABLE_STATUSES: frozenset[SecurityDiagnosisStatus] = frozenset(
    {
        SecurityDiagnosisStatus.CREATED,
        SecurityDiagnosisStatus.WAITING_FOR_INPUT,
    }
)


def to_diagnosis_evidence(diagnosis_id: str, draft: ToolEvidenceDraft) -> DiagnosisEvidence:
    """把工具证据草稿落成真实 Evidence。"""
    return DiagnosisEvidence(
        diagnosis_id=diagnosis_id,
        evidence_type=draft.evidence_type,
        source=draft.source,
        summary=draft.summary,
        payload=dict(draft.payload),
        reliability=draft.reliability,
        redacted=draft.redacted,
    )


def repair_cited_evidence_ids(
    case: SecurityDiagnosisCase,
    cited_evidence_ids: list[str],
    confidence: ConclusionConfidence,
) -> CitationRepair:
    """做最小引用修正。

    - 丢弃不属于当前诊断的引用（模型可能幻觉出 ID）；
    - 没有可用引用时：probable 取第一条设备事实 Evidence，possible 取第一条 Evidence；
    - probable 但完全没有设备事实时，降级为 possible（不允许无依据的 probable）；
    - 一条 Evidence 都没有时返回空列表，由调用方进入 inconclusive。
    """
    known = {evidence.evidence_id: evidence for evidence in case.evidence}
    kept = [evidence_id for evidence_id in cited_evidence_ids if evidence_id in known]
    repaired = kept != list(cited_evidence_ids)
    if kept:
        return CitationRepair(
            evidence_ids=kept,
            confidence=confidence,
            repaired=repaired,
            downgraded=False,
        )

    if confidence is ConclusionConfidence.PROBABLE:
        device_fact = next(
            (
                evidence
                for evidence in case.evidence
                if evidence.evidence_type in DEVICE_FACT_EVIDENCE_TYPES
            ),
            None,
        )
        if device_fact is not None:
            return CitationRepair(
                evidence_ids=[device_fact.evidence_id],
                confidence=confidence,
                repaired=True,
                downgraded=False,
            )
        first = case.evidence[0] if case.evidence else None
        if first is None:
            return CitationRepair(evidence_ids=[], confidence=confidence, repaired=True)
        # 没有设备事实，probable 不成立，降级为 possible。
        return CitationRepair(
            evidence_ids=[first.evidence_id],
            confidence=ConclusionConfidence.POSSIBLE,
            repaired=True,
            downgraded=True,
        )

    first = case.evidence[0] if case.evidence else None
    if first is None:
        return CitationRepair(evidence_ids=[], confidence=confidence, repaired=True)
    return CitationRepair(
        evidence_ids=[first.evidence_id],
        confidence=confidence,
        repaired=True,
        downgraded=False,
    )


class SecurityDiagnosisApplicationService:
    """安防设备诊断应用服务。"""

    def __init__(
        self,
        repository: InMemoryDiagnosisRepository,
        runner: ToolLoopRunner,
        registry: ToolRegistry,
        gateway: DeviceGateway,
        citation_policy: CitationPolicy | None = None,
        tool_allowlist: list[str] | None = None,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._registry = registry
        self._gateway = gateway
        self._citation_policy = citation_policy or CitationPolicy()
        self._tool_allowlist = tool_allowlist

    # ------------------------------------------------------------------ 查询
    def get_diagnosis(self, diagnosis_id: str) -> SecurityDiagnosisCase:
        return self._repository.get(diagnosis_id)

    def list_diagnoses(self) -> list[SecurityDiagnosisCase]:
        return self._repository.list()

    def list_evidence(self, diagnosis_id: str) -> list[DiagnosisEvidence]:
        return list(self._repository.get(diagnosis_id).evidence)

    # ------------------------------------------------------------------ 创建
    def create_diagnosis(
        self,
        device_id: str,
        fault_type: SecurityFaultType,
        reporter: str,
        description: str = "",
    ) -> SecurityDiagnosisCase:
        """创建一条诊断，初始状态为 created。"""
        case = SecurityDiagnosisCase(
            fault_type=fault_type,
            device_id=device_id,
            reporter=reporter,
            description=description,
        )
        return self._repository.save(case)

    # ------------------------------------------------------------------ 运行
    def run_diagnosis(self, diagnosis_id: str) -> RunDiagnosisResult:
        """启动一次诊断运行。

        流程：investigating -> Runner -> 落成 Evidence -> 落成结论
        -> CitationPolicy -> waiting_for_confirmation。
        """
        case = self._repository.get(diagnosis_id)
        if case.status not in RUNNABLE_STATUSES:
            raise InvalidStatusTransition(
                f"诊断 {case.diagnosis_id} 当前状态 {case.status.value} 不允许启动运行"
            )

        case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
        context = ToolExecutionContext(
            diagnosis_id=case.diagnosis_id,
            fault_type=case.fault_type,
            permissions=default_permissions(),
            device_gateway=self._gateway,
            metadata={"device_id": case.device_id},
        )

        result = self._runner.run(case, context, self._tool_allowlist)

        # 先把真实工具结果落成 Evidence（失败的草稿不会出现在 drafts 中）。
        for draft in result.evidence_drafts:
            case.add_evidence(to_diagnosis_evidence(case.diagnosis_id, draft))

        if not result.ok or result.final_conclusion is None:
            return self._finish_failure(
                case,
                result,
                result.error or "Runner 未产出候选结论",
                status=SecurityDiagnosisStatus.WAITING_FOR_INPUT,
            )

        draft_conclusion = result.final_conclusion
        repair = repair_cited_evidence_ids(
            case,
            draft_conclusion.cited_evidence_ids,
            draft_conclusion.confidence,
        )
        if not repair.evidence_ids:
            return self._finish_failure(
                case,
                result,
                "没有任何可用 Evidence，无法生成受控结论",
                status=SecurityDiagnosisStatus.INCONCLUSIVE,
            )

        conclusion = DiagnosisConclusion(
            diagnosis_id=case.diagnosis_id,
            fault_type=draft_conclusion.fault_type,
            summary=draft_conclusion.summary,
            root_cause=draft_conclusion.root_cause,
            confidence=repair.confidence,
            cited_evidence_ids=repair.evidence_ids,
            next_steps=list(draft_conclusion.next_steps),
            created_by="model",
        )

        # CitationPolicy 是硬闸门，任何结论都必须先过校验再落库。
        try:
            self._citation_policy.validate(conclusion, case)
        except CitationPolicyViolation as exc:
            return self._finish_failure(
                case,
                result,
                f"结论未通过 Citation Policy: {exc}",
                status=SecurityDiagnosisStatus.INCONCLUSIVE,
            )

        case.set_conclusion(conclusion)
        case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)
        self._repository.update(case)

        return RunDiagnosisResult(
            diagnosis_id=case.diagnosis_id,
            ok=True,
            status=case.status,
            evidence_count=len(case.evidence),
            conclusion=conclusion,
            citations_repaired=repair.repaired,
            confidence_downgraded=repair.downgraded,
            rounds=result.rounds,
            tool_calls=result.tool_calls,
        )

    # ------------------------------------------------------------------ 审核
    def review_diagnosis(
        self,
        diagnosis_id: str,
        action: HumanReviewAction,
        reviewer: str,
        comment: str = "",
    ) -> ReviewResult:
        """人工审核。confirmed 只能由这里产生。"""
        case = self._repository.get(diagnosis_id)
        review = HumanReview(
            diagnosis_id=case.diagnosis_id,
            action=action,
            reviewer=reviewer,
            comment=comment,
        )
        case.apply_human_review(review)
        self._repository.update(case)

        return ReviewResult(
            diagnosis_id=case.diagnosis_id,
            ok=True,
            status=case.status,
            action=action,
            review=review,
        )

    # ------------------------------------------------------------------ 报告
    def render_report(self, diagnosis_id: str) -> str:
        """渲染 Markdown 报告。"""
        from security_diagnosis_harness.application.reports import render_markdown_report

        return render_markdown_report(self._repository.get(diagnosis_id))

    # ------------------------------------------------------------------ 内部
    def _finish_failure(
        self,
        case: SecurityDiagnosisCase,
        result: ToolLoopResult,
        error: str,
        *,
        status: SecurityDiagnosisStatus,
    ) -> RunDiagnosisResult:
        """受控失败：不伪造 Evidence，只推进状态并记录错误。"""
        case.transition_to(status)
        self._repository.update(case)
        return RunDiagnosisResult(
            diagnosis_id=case.diagnosis_id,
            ok=False,
            status=case.status,
            evidence_count=len(case.evidence),
            conclusion=None,
            rounds=result.rounds,
            tool_calls=result.tool_calls,
            error=error,
        )


def build_default_budget() -> ToolLoopBudget:
    """Phase 0C 默认预算。"""
    return ToolLoopBudget(max_rounds=3, max_tool_calls=5)
