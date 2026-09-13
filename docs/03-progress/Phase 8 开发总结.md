# Phase 8 开发总结

## 1. 阶段定位

Phase 7 建立了分层数据集、确定性 Grader、版本比较门禁和低频真实模型评测；Phase 8 继续解决四个运营问题：标准答案如何产生、失败如何归因、版本趋势如何保持可比、数据集如何安全发布。

Phase 8 不增加新的设备故障域，也不通过修改 expected label 迎合模型。当前固定案例仍以脱敏合成数据为主，不能用于宣称生产准确率。

## 2. Phase 8A：标注可信度

- 盲标任务不包含 expected、来源、split 或模型输出；
- 两名不同标注员独立选择候选标签、必要工具和 Evidence；
- 标签一致率、工具/Evidence Jaccard 与 Cohen's kappa 可确定性计算；
- 一致与分歧案例都必须经过独立裁决人显式 approve；
- 裁决只生成 DatasetAdmissionCandidate，不直接修改数据集。

固定四域协议夹具得到标签一致率 0.75、工具/Evidence Jaccard 11/12、kappa 0.6923。它只证明协议可运行，不代表真实专家一致性。

## 3. Phase 8B：失败归因

- Code Grader 当前 15 个 Finding code 全部进入受治理目录；
- 统一映射到 dataset、perception、tool、evidence、conclusion、model、budget、infrastructure；
- 每项保留责任域、首次出现 commit、数据集版本和受影响案例；
- P0 严重度只继承确定性 Grader，归因层没有覆盖或降级入口；
- 未知 Finding code 直接失败，不做自由推断；
- 报告使用静态排查建议，不复制案例事实或声称未经验证的根因。

## 4. Phase 8C：历史与趋势

- JSON 历史只保存运行身份摘要、聚合指标、内容哈希、Baseline 血缘和 Gate 结论；
- 不保存案例、Finding、工具参数、Evidence、输入事实或环境变量原值；
- 同一趋势只允许代码 commit 变化；
- dataset、split、runner、model、模型参数、Prompt、配置或环境变化会明确拒绝；
- 每个候选版本强制复用 Phase 7 compare_runs，P0 和核心指标退化继续阻塞发布。

## 5. Phase 8D：数据集发布

- 发布输入包含 DatasetCase、盲标任务、两份独立标注、独立裁决、准入候选和来源谱系；
- 发布边界重新执行 Phase 8A 协议，单独伪造 Candidate 无法准入；
- 新案例与旧案例及同批案例执行身份、来源、结构指纹和同域近重复检查；
- staging 中完成三 split、Manifest、hash 与物理隔离验证后才发布目标目录；
- 新语义版本不得覆盖，来源版本保持逐字节不变；
- 回执区分 synthetic 与 authorized_export，不把合成样本冒充真实覆盖。

固定脚本临时验证 `1.0.0 → 1.1.0`，案例由 6 增至 7；仓库正式版本仍为 `1.0.0`，未经过用户内容评审前不自动提交合成扩容版本。

## 6. 已形成的闭环

```text
双人盲标 → 独立裁决 → 准入候选 → 安全版本发布
     ↑                              ↓
失败归因 ← Code Grader ← 运行历史与单变量趋势
                         ↓
                    Phase 7 发布门禁
```

这条链路证明评测标准、失败原因、趋势比较和数据发布都有确定性工程边界，而不是由 LLM 自行解释或修改。

## 7. 当前明确边界

- 正式 Runtime 仍只支持摄像头黑屏域；
- 正式 Runtime 仍使用 StaticDeviceGateway 与 FakeLLM；
- 四域能力主要通过独立评测 Container 和固定案例验证；
- Phase 7 真实模型基线只有两个 Validation 案例；
- 固定数据集只有 6 个合成协议案例；
- BGE 混合检索完成了适配与离线评测，但不是默认 Runtime 依赖；
- 项目尚未接入真实安防平台、真实设备或生产工单反馈。

## 8. 结论

Phase 8 让项目从“有评测脚本”演进为“评测标准可追溯、失败可归因、趋势可比较、数据可治理发布”的工程基线。它提升的是可信迭代能力，不等同于证明真实设备诊断效果；后续必须通过严格审计和真实只读设备接入验证企业价值。
