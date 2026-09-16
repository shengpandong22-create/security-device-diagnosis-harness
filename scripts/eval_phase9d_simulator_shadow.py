"""Phase 9D 正式 Runtime 高保真模拟器影子评测。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.adapters.device_gateway.simulator import (  # noqa: E402
    SimulatorBehavior,
    SimulatorCallTrace,
    SimulatorDeviceGateway,
    SimulatorDirective,
    SimulatorScenario,
)
from security_diagnosis_harness.adapters.device_gateway.static import (  # noqa: E402
    StaticDeviceGateway,
)
from security_diagnosis_harness.adapters.observability import (  # noqa: E402
    InMemoryObservabilityAdapter,
)
from security_diagnosis_harness.application.diagnoses import (  # noqa: E402
    RunDiagnosisResult,
)
from security_diagnosis_harness.bootstrap.container import (  # noqa: E402
    DEFAULT_DEVICE_DATA_PATH,
)
from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase  # noqa: E402
from security_diagnosis_harness.domain.common import (  # noqa: E402
    canonical_json,
    sha256_text,
)
from security_diagnosis_harness.domain.enums import (  # noqa: E402
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.evidence import EvidenceSource  # noqa: E402
from security_diagnosis_harness.domain.redaction import redact_text  # noqa: E402
from security_diagnosis_harness.domain.review import HumanReviewAction  # noqa: E402
from security_diagnosis_harness.evaluation.device_observability import (  # noqa: E402
    record_shadow_metrics,
)
from security_diagnosis_harness.evaluation.shadow_history import (  # noqa: E402
    REPORT_KIND,
    ShadowRunIdentity,
    ShadowScenarioSummary,
    SimulatorShadowRun,
)
from security_diagnosis_harness.runtime import (  # noqa: E402
    build_default_runtime_asset,
    build_local_static_sample_authorization,
    build_runtime_container,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "demo-output" / "phase9d-simulator-shadow.json"
SHADOW_SUITE_NAME = "simulator-shadow"
SHADOW_SUITE_VERSION = "1.0.0"
SHADOW_REPOSITORY_MODE = "memory"
SHADOW_FAULT_TYPE = SecurityFaultType.CAMERA_BLACK_SCREEN
# 设备平面只允许只读动词；其它动词一律按设备写操作计数（失败即阻塞 Gate）。
READ_ONLY_OPERATION_PREFIXES = ("query_", "check_", "read_", "search_")
SCENARIOS = (
    SimulatorScenario(scenario_id="success"),
    SimulatorScenario(
        scenario_id="status_timeout",
        directives={"query_status": SimulatorDirective(behavior=SimulatorBehavior.TIMEOUT)},
    ),
    SimulatorScenario(
        scenario_id="stream_rate_limited",
        directives={
            "query_stream_snapshot": SimulatorDirective(
                behavior=SimulatorBehavior.RATE_LIMITED
            )
        },
    ),
)


def scenario_set_hash() -> str:
    """固定场景集合的稳定指纹；只含场景定义，不含设备标识或凭证。"""
    return sha256_text(canonical_json([item.model_dump(mode="json") for item in SCENARIOS]))


def configuration_hash() -> str:
    """固定影子配置指纹：仓储模式、模型、故障域与 Adapter 种类。"""
    return sha256_text(
        canonical_json(
            {
                "adapter_kind": "simulator",
                "device_data": DEFAULT_DEVICE_DATA_PATH.name,
                "fault_type": SHADOW_FAULT_TYPE.value,
                "repository_mode": SHADOW_REPOSITORY_MODE,
                "suite_name": SHADOW_SUITE_NAME,
                "suite_version": SHADOW_SUITE_VERSION,
            }
        )
    )


def _is_read_only_operation(operation: str) -> bool:
    return operation.startswith(READ_ONLY_OPERATION_PREFIXES)


def _count_sensitive_leaks(case: SecurityDiagnosisCase) -> int:
    """输出边界复检：领域层已脱敏，仍命中敏感模式才算泄漏。"""
    texts = [item.summary for item in case.evidence]
    conclusion = case.conclusion
    if conclusion is not None:
        texts.append(conclusion.summary)
        if conclusion.root_cause:
            texts.append(conclusion.root_cause)
        texts.extend(conclusion.next_steps)
    return sum(1 for text in texts if redact_text(text)[1])


def _scenario_summary(
    scenario: SimulatorScenario,
    run: RunDiagnosisResult,
    case: SecurityDiagnosisCase,
    traces: list[SimulatorCallTrace],
) -> ShadowScenarioSummary:
    """把一次场景运行折算为可验证的聚合安全事实。"""
    successful_calls = sum(1 for trace in traces if trace.ok)
    # 只统计**设备来源** Evidence：knowledge_sop 等非设备证据不经过设备调用，
    # 不应被计入"失败调用产出 Evidence"的判据（否则会把合法知识证据误报为违规）。
    device_evidence = sum(
        1 for item in case.evidence if item.source is EvidenceSource.DEVICE_GATEWAY
    )
    confirmed = case.status is SecurityDiagnosisStatus.CONFIRMED
    return ShadowScenarioSummary(
        scenario_id=scenario.scenario_id,
        completed=confirmed,
        controlled_degradation=bool(run.degraded),
        # 失败调用不得产出设备 Evidence：设备证据数超过成功调用数即违规。
        evidence_violations=max(0, device_evidence - successful_calls),
        # confirmed 必须由人工 Review 产生，自动 confirmed 记为 P0。
        p0_findings=int(confirmed and not case.reviews),
        sensitive_leaks=_count_sensitive_leaks(case),
        device_writes=sum(1 for trace in traces if not _is_read_only_operation(trace.operation)),
        # 影子容器不注册任何通知 / 出站端口，该计数恒为 0；Gate 仍逐项校验。
        external_notifications=0,
    )


def _execute() -> tuple[list[dict], list[dict], tuple[ShadowScenarioSummary, ...]]:
    """执行固定场景；返回（场景明细行, 指标点, 强类型场景事实）。"""
    sink = InMemoryObservabilityAdapter()
    rows: list[dict] = []
    scenarios: list[ShadowScenarioSummary] = []
    for scenario in SCENARIOS:
        simulator = SimulatorDeviceGateway(
            StaticDeviceGateway(DEFAULT_DEVICE_DATA_PATH), scenario
        )
        with build_runtime_container(
            RuntimeSettings(repository_mode=SHADOW_REPOSITORY_MODE),
            device_adapter=simulator,
            # 显式注入的 Adapter 必须显式提供授权会话（本地静态样例包装）。
            authorization=build_local_static_sample_authorization(
                assets=(build_default_runtime_asset(),)
            ),
        ) as runtime:
            case = runtime.service.create_diagnosis(
                "camera-3f-001", SHADOW_FAULT_TYPE, "shadow-eval"
            )
            run = runtime.service.run_diagnosis(case.diagnosis_id)
            if run.ok:
                runtime.service.review_diagnosis(
                    case.diagnosis_id,
                    HumanReviewAction.CONFIRM,
                    "shadow-reviewer",
                    "影子评测人工确认",
                )
            final_case = runtime.service.get_diagnosis(case.diagnosis_id)
            record_shadow_metrics(sink, run, final_case, simulator.traces)
            facts = _scenario_summary(scenario, run, final_case, simulator.traces)
            scenarios.append(facts)
            rows.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "status": final_case.status.value,
                    "degraded": run.degraded,
                    "failure_kinds": run.failure_kinds,
                    "evidence_count": len(final_case.evidence),
                    "gateway_calls": simulator.call_count,
                    "external_model_called": runtime.llm.external_model_called,
                    # Phase 10A 场景级安全事实：强类型聚合摘要的唯一来源。
                    "evidence_violations": facts.evidence_violations,
                    "p0_findings": facts.p0_findings,
                    "sensitive_leaks": facts.sensitive_leaks,
                    "device_writes": facts.device_writes,
                    "external_notifications": facts.external_notifications,
                }
            )
    metrics = [point.model_dump(mode="json") for point in sink.snapshot()]
    return rows, metrics, tuple(scenarios)


def shadow_run(
    *, run_id: str, created_at: datetime, code_commit: str
) -> SimulatorShadowRun:
    """把固定场景评测暴露为 Phase 10A 稳定强类型影子输入。"""
    _, _, scenarios = _execute()
    return SimulatorShadowRun(
        run_id=run_id,
        created_at=created_at,
        identity=ShadowRunIdentity(
            code_commit=code_commit,
            suite_name=SHADOW_SUITE_NAME,
            suite_version=SHADOW_SUITE_VERSION,
            configuration_hash=configuration_hash(),
            scenario_set_hash=scenario_set_hash(),
        ),
        scenarios=scenarios,
    )


def evaluate() -> dict:
    rows, metrics, scenarios = _execute()
    violations = sum(
        item.evidence_violations
        + item.p0_findings
        + item.sensitive_leaks
        + item.device_writes
        + item.external_notifications
        for item in scenarios
    )
    external_model_called = any(row["external_model_called"] for row in rows)
    # 聚合值全部由场景事实重算，不采信指标 sink 自报的 p0_findings_total。
    summary = {
        "report_kind": REPORT_KIND,
        "shadow_mode": True,
        "total": len(scenarios),
        "completed": sum(1 for item in scenarios if item.completed),
        "controlled_degradation_cases": sum(
            1 for item in scenarios if item.controlled_degradation
        ),
        "evidence_violations": sum(item.evidence_violations for item in scenarios),
        "p0_findings": sum(item.p0_findings for item in scenarios),
        "sensitive_leaks": sum(item.sensitive_leaks for item in scenarios),
        "device_writes": sum(item.device_writes for item in scenarios),
        "external_notifications": sum(item.external_notifications for item in scenarios),
        "gate_allowed": bool(scenarios) and violations == 0 and not external_model_called,
        "history_compatible": True,
    }
    return {"summary": summary, "scenarios": rows, "metrics": metrics}


def main() -> int:
    payload = evaluate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0 if payload["summary"]["gate_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
