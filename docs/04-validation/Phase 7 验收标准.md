# Phase 7 验收标准

## 1. Phase 7A 数据集

- [x] Dev / Validation / Test 物理隔离
- [x] 近重复与同源模板不会跨集合
- [x] 案例包含期望结果、证据、工具、禁止行为和预算
- [x] 数据集具有稳定版本和变更记录

Phase 7A 补充验收：Test Set 默认不可读取，只有发布门禁显式传入 `allow_test=True` 才能获得案例；manifest 对每个案例文件执行 SHA-256 校验；当前 `1.0.0` 是 6 个脱敏合成案例组成的协议基线，不是生产准确率样本。

## 2. Phase 7B Grader

- [x] 端到端与步骤级指标均可计算
- [x] 权限、敏感信息、confirmed 越权由代码规则判定
- [x] 工具选择、参数、重复调用和 Evidence 链可评分
- [x] Grader 自身有正反例测试

Phase 7B 补充验收：`CodeBasedGrader` 同时输出单案例 Findings 和 Suite 聚合指标；自动 confirmed、敏感泄漏、越权工具、跨故障域、轮次/工具/模型/超时预算超限均为 P0；候选准确率与 macro-F1、Citation、unsupported claim、工具 precision/recall、参数、重复失败、Evidence 覆盖/归属/可信度、轮次、时延和成本均可确定性计算。模型 Grader 不得覆盖这些结果。

## 3. Phase 7C 版本门禁

- [x] 报告绑定 commit、数据集、模型、Prompt hash 和配置
- [x] candidate 与 baseline 保持单变量比较
- [x] 输出逐案例新增失败与已修复问题
- [x] 任一 P0 失败阻塞发布
- [x] 核心指标退化超过容差时进程返回非零

Phase 7C 补充验收：`EvaluationRun` 不保存原始输入或密钥；比较时除 `code_commit` 外的实验变量必须完全一致，案例集合也必须一致；报告同时输出 JSON 与 Markdown，并列出指标 delta、逐案例新增失败和已修复问题。仓库基线明确标注为确定性协议基线，不冒充真实模型效果。

## 4. Phase 7D 真实模型与人工复核

- [x] 真实模型输入经过脱敏与字段白名单
- [x] 调用次数、超时、token 和成本受预算限制
- [x] 真实模型报告与 Fake 基线明确分离
- [x] disputed 案例经过人工复核才能进入正式数据集

Phase 7D 工程验收已完成：外部模型入口默认关闭，必须显式传入 `--execute-real-model`；Validation/Test 每案例最多一次调用且不重试，报告记录模型版本、token、时延、错误类型和估算成本。当前环境未配置真实模型变量，因此没有伪造真实模型效果基线；配置后的首次真实运行属于独立运营验收。

## 5. 总体验收

- [x] 能回答一次版本变更修复了什么、退化了什么
- [x] 评测失败能够定位到规则、工具、Evidence、模型或检索阶段
- [x] 固定案例满分不再被描述为生产准确率
- [x] 不泄漏真实设备数据、凭证和模型密钥
