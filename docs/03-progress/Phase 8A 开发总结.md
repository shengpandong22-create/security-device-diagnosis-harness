# Phase 8A 开发总结

## 1. 交付结果

Phase 8A 将“标准答案”从一个静态字段提升为可解释的人工治理流程：盲标任务不携带 expected 或模型答案，两名不同标注员独立提交，独立裁决人显式决定是否形成准入候选。

## 2. 三个最小闭环

| 阶段 | 结果 |
| --- | --- |
| 8A-1 | 盲标任务、目录约束、双人分歧和一致性指标 |
| 8A-2 | approve/reject/needs_revision 裁决与只读 DatasetAdmissionCandidate |
| 8A-3 | 四域合成固定盲标对、JSON/Markdown 报告和可重复验收脚本 |

## 3. 固定协议结果

- 任务数：4；
- 标签一致率：0.75；
- 工具 Jaccard：11/12；
- Evidence Jaccard：11/12；
- Cohen's kappa：0.6923；
- 待裁决率：0.5。

这些数字来自 `synthetic_protocol_fixture`，只证明计算、分歧和裁决协议可运行，不代表真实安防专家的一致性水平。

## 4. 安全边界

- 报告不保存 input facts、reviewer 或 rationale；
- Task、Annotation、Decision 在边界重新校验；
- 标注一致也不会自动写入数据集；
- 准入候选不包含 split、版本或写盘方法；
- 全过程离线，不访问 LLM、BGE 或真实设备。
