# Phase 8 验收标准

## 1. Phase 8A 双人盲标与裁决

- [x] AnnotationTask 不包含 expected、模型输出或 Test Set 答案
- [x] 两名不同标注员独立提交，标签和引用值均受目录约束
- [x] 标签、工具、Evidence 分歧可确定性计算
- [x] 无分歧仍需显式裁决，有分歧无裁决不得准入
- [x] 裁决只产生 DatasetAdmissionCandidate，不自动写数据集
- [x] 标注一致率、Jaccard 和标签 kappa 可计算

Phase 8A-1 已完成：盲标任务不复制 expected、source、split 或模型输出；候选标签、工具和 Evidence 必须来自完整目录；同一 reviewer 不能冒充双人标注；报告输出标签一致率、工具/Evidence Jaccard、置信度差、Cohen's kappa 和裁决率。裁决及数据集准入候选留给 8A-2。

Phase 8A-2 已完成：approve/reject/needs_revision 具有明确语义；裁决人必须独立于两名盲标员；一致与分歧案例都必须显式 approve 才能生成 `DatasetAdmissionCandidate`；候选对象不包含事实、数据集版本或 split，也不具备写文件能力。所有 Task、Annotation 和 Decision 在裁决边界重新构造校验，阻断 `model_copy` 绕过。

## 2. Phase 8B 失败归因

- [ ] 每条 Finding 映射到稳定阶段和责任域
- [ ] P0 不可被模型评分覆盖或降级
- [ ] 失败报告可定位首次出现版本与受影响案例
- [ ] 修复建议不包含敏感输入和未经证实的根因

## 3. Phase 8C 历史与趋势

- [ ] 运行摘要持久化但不保存事实、密钥和设备凭证
- [ ] 只有单变量可比运行进入趋势
- [ ] 数据集、模型、Prompt 或配置变化均可追踪
- [ ] 趋势退化可触发现有发布门禁

## 4. Phase 8D 数据集发布

- [ ] 新案例经过脱敏、来源、双人标注、裁决和泄漏检查
- [ ] 发布新语义版本且旧版本不可变
- [ ] Validation/Test 继续物理隔离
- [ ] 真实模型仍为显式授权、单次调用、无自动重试
- [ ] 报告明确区分合成样本覆盖与真实业务覆盖

## 5. 总体验收

- [ ] 能证明“标准答案如何产生”，而不只是展示准确率数字
- [ ] 能解释一次退化属于数据、工具、证据、模型还是基础设施
- [ ] 不通过修改标准答案迎合模型
- [ ] 不把小样本满分描述成生产准确率
