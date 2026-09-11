"""Keyword / BGE Vector / Hybrid 三路知识检索固定评测。"""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from security_diagnosis_harness.adapters.embedding.http_bge import (
    HttpBgeEmbeddingAdapter,
)
from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
)
from security_diagnosis_harness.application.hybrid_knowledge_retriever import (
    HybridKnowledgeRetriever,
    SemanticKnowledgeRetriever,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeReview,
    KnowledgeReviewAction,
)
from security_diagnosis_harness.ports.embedding import EmbeddingPort

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "samples" / "knowledge" / "retrieval_eval_cases.json"
OUTPUT_PATH = ROOT / "demo-output" / "phase5-knowledge-retrieval-eval.json"

KNOWLEDGE: dict[SecurityFaultType, list[tuple[str, str, str]]] = {
    SecurityFaultType.CAMERA_BLACK_SCREEN: [
        (
            "device_offline_or_network_unreachable",
            "设备离线或网络不可达",
            "摄像机无法连接，平台预览黑屏",
        ),
        ("stream_publish_or_encoder_issue", "码流发布或编码器异常", "设备在线但没有视频流输出"),
        ("platform_pull_or_access_path_issue", "平台拉流链路异常", "设备有码流但平台取流失败"),
    ],
    SecurityFaultType.RECORDING_MISSING: [
        ("recording_plan_disabled", "录像计划被禁用", "没有安排录像任务"),
        (
            "storage_capacity_or_pool_issue",
            "存储容量或存储池异常",
            "磁盘空间耗尽或存储节点离线导致没有历史视频",
        ),
        ("playback_index_or_file_issue", "回放索引或文件异常", "录像文件存在但时间轴无法检索回放"),
    ],
    SecurityFaultType.ACCESS_CARD_FAILED: [
        ("permission_not_granted", "门禁权限未授予", "人员没有目标门权限"),
        ("controller_offline_or_no_response", "控制器离线或无响应", "刷卡后门禁设备完全没有反应"),
        ("access_time_window_denied", "门禁授权时段拒绝", "仅允许指定时间通行，其他时段刷卡被拒绝"),
    ],
    SecurityFaultType.ALARM_FALSE_POSITIVE: [
        ("alarm_rule_too_sensitive", "报警规则过于灵敏", "阈值过低导致轻微信号触发告警"),
        ("environment_interference", "雨雾强光等环境干扰", "天气和光照变化造成莫名报警"),
        ("duplicate_alarm_burst", "短时间重复告警风暴", "同一事件连续产生大量重复告警"),
    ],
}


def build_repository() -> InMemoryKnowledgeRepository:
    repository = InMemoryKnowledgeRepository()
    for fault_type, items in KNOWLEDGE.items():
        for index, (label, title, summary) in enumerate(items):
            candidate = KnowledgeCandidate(
                knowledge_id=f"knw-{fault_type.value}-{index}",
                fault_type=fault_type,
                candidate_label=label,
                title=title,
                summary=summary,
                symptoms=[summary],
                root_cause=title,
                troubleshooting_steps=["核对对应设备事实并由人工复核"],
                source_diagnosis_id=f"diag-{fault_type.value}-{index}",
                source_conclusion_id=f"con-{fault_type.value}-{index}",
                source_evidence_ids=[f"evd-{fault_type.value}-{index}"],
            )
            candidate.apply_review(
                KnowledgeReview(
                    knowledge_id=candidate.knowledge_id,
                    action=KnowledgeReviewAction.CONFIRM,
                    reviewer="eval-expert",
                )
            )
            repository.save(candidate)
    return repository


def evaluate(
    base_url: str = "http://127.0.0.1:18080",
    embedding: EmbeddingPort | None = None,
) -> dict[str, Any]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    repository = build_repository()
    selected_embedding = embedding or HttpBgeEmbeddingAdapter(
        base_url=base_url, timeout_seconds=30.0
    )
    retrievers = {
        "keyword": repository,
        "vector": SemanticKnowledgeRetriever(repository, selected_embedding),
        "hybrid": HybridKnowledgeRetriever(repository, selected_embedding),
    }
    cold_started = perf_counter()
    selected_embedding.embed_query("模型预热")
    cold_start_ms = (perf_counter() - cold_started) * 1000
    report: dict[str, Any] = {
        "total": len(cases),
        "bge_cold_start_ms": round(cold_start_ms, 2),
        "modes": {},
        "cases": [],
    }
    for mode, retriever in retrievers.items():
        reciprocal_ranks: list[float] = []
        recall1 = 0
        recall3 = 0
        started = perf_counter()
        mode_cases = []
        for case in cases:
            results = retriever.search_confirmed(
                case["query"], SecurityFaultType(case["fault_type"]), 3
            )
            labels = [item.candidate_label for item in results]
            rank = (
                labels.index(case["expected_label"]) + 1
                if case["expected_label"] in labels
                else 0
            )
            recall1 += int(rank == 1)
            recall3 += int(0 < rank <= 3)
            reciprocal_ranks.append(1.0 / rank if rank else 0.0)
            mode_cases.append({"case_id": case["case_id"], "rank": rank, "labels": labels})
        elapsed_ms = (perf_counter() - started) * 1000
        report["modes"][mode] = {
            "recall_at_1": recall1 / len(cases),
            "recall_at_3": recall3 / len(cases),
            "mrr": sum(reciprocal_ranks) / len(cases),
            "total_elapsed_ms": round(elapsed_ms, 2),
            "average_elapsed_ms": round(elapsed_ms / len(cases), 2),
        }
        report["cases"].append({"mode": mode, "results": mode_cases})
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
