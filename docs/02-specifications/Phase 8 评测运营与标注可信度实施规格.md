# Phase 8 评测运营与标注可信度实施规格

## 1. 阶段定位

Phase 7 解决了“如何执行和比较评测”，Phase 8 解决“标准答案由谁确认、失败如何归因、趋势如何追踪、数据如何安全扩充”。目标不是继续制造满分，而是让评测结论具备可审核的人工作业链路。

当前基线只有 6 个脱敏合成案例、2 个真实模型 Validation 案例。它足以证明协议，不足以证明生产准确率。Phase 8 不允许通过修改 expected label 迎合模型，也不允许把模型输出直接写回正式数据集。

## 2. 总体路线

| 阶段 | 核心交付 | 明确不做 |
| --- | --- | --- |
| 8A | 双人盲标、分歧检测、人工裁决、准入候选 | 不自动改数据集，不让标注员看模型答案 |
| 8B | 失败分类目录、阶段归因、可行动修复建议 | 不用 LLM 覆盖 Code Grader 的确定性结论 |
| 8C | 评测运行历史、趋势、基线血缘和可比性检查 | 不引入 Prometheus/Grafana 等重型平台 |
| 8D | 受治理的数据集扩容、版本发布与低频真实回归 | 不把合成样本数量包装成真实覆盖率 |

## 3. Phase 8A：双人盲标与裁决

### 3.1 领域对象

- `AnnotationTask`：只包含脱敏事实、故障域、完整标签目录、工具/Evidence 目录，不含 expected 或模型输出；
- `BlindAnnotation`：标注员独立提交候选标签、必要工具、必要 Evidence、理由和置信度；
- `AnnotationDisagreement`：确定性计算标签、工具和 Evidence 分歧；
- `AdjudicationDecision`：第三方或负责人明确选择最终结论并说明理由；
- `DatasetAdmissionCandidate`：裁决完成后的只读候选，仍需现有数据集隔离检查后才能入库。

### 3.2 强制边界

1. 同一标注员不能提交两份意见冒充双人复核；
2. reviewer 身份、理由和自由文本进入对象前脱敏；
3. 无分歧时也要显式确认，不把“一致”当自动批准；
4. 有分歧但没有裁决时不得生成准入候选；
5. 裁决只能从故障域候选标签目录中选择；
6. 标注结果不能包含 Test Set expected 或真实模型输出；
7. 准入候选不写文件、不更新 manifest、不递增数据集版本。

### 3.3 一致性指标

- 标签完全一致率；
- 工具集合 Jaccard；
- Evidence 类型集合 Jaccard；
- 置信度差；
- Cohen's kappa（标签维度，样本足够时）；
- 裁决率与分歧原因分布。

## 4. Phase 8B：失败归因

将 Phase 7 的 Findings 映射到 `dataset / perception / tool / evidence / conclusion / model / budget / infrastructure` 八类阶段。每个失败必须保留 case、run identity、确定性 finding、首次出现版本和建议负责人。P0 不允许被模型 Grader 降级。

## 5. Phase 8C：评测历史与趋势

使用本地 JSON/SQLite 保存脱敏运行摘要，不保存输入事实和密钥。只有数据集版本、模型、Prompt hash、配置一致的运行可以画趋势；不可比运行必须明确拒绝，不允许把不同变量混成“版本提升”。

## 6. Phase 8D：数据集扩容与发布

新增案例先进入 candidate 区，经双人盲标、裁决、脱敏、来源记录、近重复检查后进入新语义版本。Validation/Test 的真实模型运行仍需人工授权，每案例一次、不自动重试。Test Set 只用于发布门禁，不用于 Prompt 调优。

## 7. 开发顺序

1. 8A-1：AnnotationTask、BlindAnnotation、分歧计算；
2. 8A-2：Adjudication 与 DatasetAdmissionCandidate；
3. 8A-3：一致性报告和固定案例；
4. 8B：失败归因；
5. 8C：运行历史；
6. 8D：数据集 1.1.0 候选发布。

## 8. 安全与验收原则

- 全部自动测试离线；
- 不访问真实设备、BGE 或 LLM；
- 不读取 `.env`；
- 不提交 API Key、设备凭证或未脱敏业务数据；
- 先写失败测试，再实现；
- 每个阶段通过 ruff、专项测试、全量测试和 `git diff --check`。
