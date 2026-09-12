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

## 3. 关键安全边界

- Test Set 默认不可读，发布门禁必须显式授权；
- expected label、必要 Evidence、来源和模板 ID 不发送给真实模型；
- 自动 confirmed、敏感泄漏、越权工具、跨域和预算超限由代码规则判为 P0；
- 模型返回的说明、标签、工具名、Evidence 类型和模型版本都再次脱敏；
- 真实模型调用必须显式传入开关，每案例一次，不自动重试；
- 人工 approve 只生成准入候选，不自动修改正式数据集或版本。

## 4. 当前未完成的外部验收

当前机器没有配置 `SECURITY_DIAGNOSIS_EVAL_BASE_URL`、`SECURITY_DIAGNOSIS_EVAL_MODEL` 和 `SECURITY_DIAGNOSIS_EVAL_API_KEY`，因此本阶段没有访问外部模型，也没有生成真实模型效果基线。这是诚实的环境边界，不影响离线工程验收；首次真实运行需单独授权并保留报告。

## 5. 后续演进建议

1. 扩充人工标注的 Validation/Test 样本，覆盖更多设备型号、时间边界与故障组合；
2. 对首次真实模型运行建立独立 Baseline，后续保持模型、Prompt 和数据集单变量比较；
3. 引入双人争议复核和标注一致性指标；
4. 将 Phase 7C CLI 接入 CI，但保持 Test Set 与真实模型密钥由受控环境提供。
