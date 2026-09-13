# Phase 7 开发总结

## 1. 交付定位

Phase 7 把项目从“固定案例能够回归”推进到“数据集受治理、行为可分层评分、版本可比较、退化可阻断”。它建立的是评测工程基线，不等于已经证明生产准确率。

## 2. 分阶段结果

| 阶段 | 结果 |
| --- | --- |
| 7A | Dev / Validation / Test 物理隔离，来源谱系、语义 hash、同域近重复和 Test Set 访问受控 |
| 7B | Code-based Grader 覆盖端到端结果、工具轨迹、Evidence、预算、时延与成本 |
| 7C | Baseline/Candidate 单变量比较，逐案例差异报告，P0 与核心指标退化门禁 |
| 7D | OpenAI-compatible 低频评测入口、白名单输入、单次无重试、token/成本预算、人工争议准入 |

### 7D 真实模型协议收尾

这里的“真实模型”只表示请求由真实 OpenAI-compatible 模型完成。评测输入是脱敏后的受限事实，输出是候选标签、拟选择工具与拟引用 Evidence 类型；该脚本没有执行正式 ToolLoopRunner、真实工具或 DeviceGateway，因此属于结构化诊断决策基线，不是端到端 Agent 评测。

首次 `deepseek-flash` Validation 实测完成 2 次调用且无协议失败。初始精确标签准确率为 0.5，其中报警案例输出与标准标签语义接近但命名越界。项目没有修改标准答案迎合模型，而是补齐以下治理：

- 向模型公开故障域完整候选标签和标准 Evidence 类型，不发送案例期望答案；
- 候选标签、工具和 Evidence 类型执行确定性枚举校验；
- Candidate Accuracy、Tool Precision/Recall、Evidence Compliance/Recall 分开统计；
- 合法但未命中、或疑似语义等价的标签生成 `review_pending` 项，不自动归一化或修改数据集。

增强协议后的受控复验结果为：2/2 调用成功、Candidate Accuracy 1.0、Tool Precision 0.75、Tool Recall 1.0、Evidence Compliance/Recall 1.0、无待复核案例。报警案例额外选择了两个允许但非必要的工具，因此工具精确率不是满分；这属于效率优化项，不是越权或诊断错误。脱敏结果已固化为 `evaluation-baselines/phase7/validation-1.0.0-deepseek-flash.json`。

## 3. 关键安全边界

- Test Set 默认不可读，发布门禁必须显式授权；
- expected label、必要 Evidence、来源和模板 ID 不发送给真实模型；
- 自动 confirmed、敏感泄漏、越权工具、跨域和预算超限由代码规则判为 P0；
- 模型返回的说明、标签、工具名、Evidence 类型和模型版本都再次脱敏；
- 真实模型调用必须显式传入开关，每案例一次，不自动重试；
- 人工 approve 只生成准入候选，不自动修改正式数据集或版本。

## 4. 当前未完成的外部验收

已使用 `deepseek-flash` 完成受控 Validation 协议验证及增强后复验：每轮 2 个案例、每案例一次、无自动重试。原始运行报告位于被 Git 忽略的 `demo-output/`，版本化基线只保留脱敏评分结果，不包含输入事实、API Key 或服务地址。该小样本只能证明真实链路和评测协议可运行，不能作为生产准确率证明。由于未配置 token 单价，成本仍显示为 0，暂不具备成本基线意义。

## 5. 后续演进建议

1. 扩充人工标注的 Validation/Test 样本，覆盖更多设备型号、时间边界与故障组合；
2. 对首次真实模型运行建立独立 Baseline，后续保持模型、Prompt 和数据集单变量比较；
3. 引入双人争议复核和标注一致性指标；
4. 将 Phase 7C CLI 接入 CI，但保持 Test Set 与真实模型密钥由受控环境提供。
