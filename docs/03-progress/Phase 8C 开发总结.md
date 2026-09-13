# Phase 8C 开发总结

## 1. 阶段目标

Phase 8C 把单次评测升级为可审核的版本历史与趋势，但不引入监控平台，也不把不同实验变量的结果混成“版本提升”。

## 2. 已完成能力

- `EvaluationRunSummary`：保存受控身份、聚合指标、内容哈希和确定性 P0 数量；
- `JsonEvaluationHistory`：原子追加本地 JSON 历史，重复 run、未知 Baseline 或哈希不一致均受控拒绝；
- `comparison_changes()`：明确追踪 dataset、split、runner、model、model parameters、Prompt、configuration 与 environment 的变化；
- `EvaluationTrendReport`：输出可比版本的核心指标、Baseline 血缘和阻塞版本；
- 每个非首运行强制复用 Phase 7 `compare_runs()`，P0 或核心指标退化直接进入 BLOCKED 状态。

## 3. 数据安全边界

历史文件不保存 `grade.cases`、Finding、工具参数、Evidence、输入事实或环境变量原值。运行身份在历史写入边界重新校验，阻断通过 `model_copy` 注入敏感配置；环境只保存哈希。加载外部历史时同样重新执行协议校验。

## 4. 可比性边界

同一趋势只允许代码 commit 发生变化。数据集版本、split、Runner、模型、模型参数、Prompt hash、configuration hash 或 environment 任一变化，都会返回具体变化字段并拒绝写入。需要研究其它变量时，应创建新的独立趋势序列。

## 5. 固定验证

```powershell
uv run ruff check .
uv run pytest
uv run python scripts/eval_phase8_history_trend.py
```

固定脚本使用确定性 Grader 构造一个通过的 Baseline 与一个故意退化的 Candidate，验证 Gate 阻塞，不调用真实模型、BGE 或设备。

## 6. 下一阶段

Phase 8D 将实现数据集候选区、脱敏与来源验证、双人标注裁决准入、近重复检查、语义版本发布及 Validation/Test 物理隔离。
