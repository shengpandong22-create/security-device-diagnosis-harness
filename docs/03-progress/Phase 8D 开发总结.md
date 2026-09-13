# Phase 8D 开发总结

## 1. 阶段目标

Phase 8D 把“人工裁决后的候选”升级为可审核、不可覆盖且保持集合隔离的数据集发布流程。重点不是增加一个样本，而是证明标准答案的产生过程能够被机器复验。

## 2. 完整准入证明

每个新增案例必须同时提供：DatasetCase、脱敏盲标任务、两名不同标注员的意见、独立裁决、Phase 8A 生成的 DatasetAdmissionCandidate，以及 synthetic 或 authorized_export 来源谱系。发布器会重新执行盲标和裁决并逐字段对比准入候选，伪造候选状态、标签、工具、Evidence 或裁决 ID 都会失败。

## 3. 数据与来源治理

- DatasetCase 在发布边界重新构造，阻断构造后敏感数据注入；
- source 与 source_record_id 同样执行敏感文本检查；
- 来源记录必须与 SourceProvenance 一致；
- synthetic 与 authorized_export 明确区分，不允许相互伪装；
- 发布回执只保留治理 ID 和数量，不保存 input_facts、标注理由或人员名称。

## 4. 泄漏与近重复检查

新增案例与全部旧案例、同批新增案例比较 case_id、template_group_id、来源记录、结构指纹及同故障域字符 n-gram 相似度。同 split 内复制改名同样会被阻断，补齐了仅依赖跨 split 注册表无法覆盖的发布风险。

## 5. 不可变发布

目标版本必须是高于来源版本的三段语义版本，且至少包含一个治理后新增案例。发布器先在 staging 生成三个物理 split、案例文件、Manifest/hash 和安全发布回执，再从磁盘完整加载验证。只有全部通过才将 staging 原子重命名为目标版本；目标存在时拒绝覆盖，来源版本文件保持逐字节不变。

## 6. 固定验证边界

```powershell
uv run ruff check .
uv run pytest
uv run python scripts/eval_phase8_dataset_release.py
```

固定脚本用一个合成 Dev 案例演示 `1.0.0 → 1.1.0` 临时发布，总数由 6 增至 7，Validation/Test 仍各 2 个。它不访问真实模型、BGE 或设备，不读取 Test 标准答案，也不把合成样本冒充真实业务覆盖。

## 7. 阶段结论

Phase 8A～8D 已将标准答案形成、失败归因、版本趋势和数据集发布串成闭环。仓库正式数据集仍保持 `1.0.0`；临时 `1.1.0` 只是发布协议验收结果，后续真实扩容必须经过人工内容审核。
