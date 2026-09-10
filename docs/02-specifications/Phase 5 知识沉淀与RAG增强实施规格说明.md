# Phase 5 知识沉淀与 RAG 增强实施规格说明

> 文档状态：待实施基线  
> 适用项目：Security Device Diagnosis Harness  
> 目标阶段：Phase 5A / 5B / 5C  

## 1. 背景

Phase 0～4 已经完成四类安防运维诊断域：

- 摄像头黑屏；
- 录像缺失；
- 门禁刷卡异常；
- 报警误报。

当前系统已经具备 Harness 约束能力：

- LLM 只能在受控工具、预算和 CitationPolicy 内推理；
- 工具输出必须落成 Evidence；
- 结论必须引用 Evidence；
- `confirmed` 只能由人工审核产生；
- Phase 1～4 都有固定案例评测。

但当前系统还有一个明显短板：每次诊断完成后，确认过的经验不会沉淀为可复用知识。`knowledge__search` 仍然主要是静态 SOP，无法体现“越诊断越有经验”。

Phase 5 的目标就是补上这条闭环：

```text
诊断完成
  -> 人工 confirm
  -> 生成 Knowledge Candidate
  -> 人工审核知识候选
  -> confirmed knowledge
  -> knowledge__search 召回
  -> 支撑下一次诊断
```

## 2. 总体原则

### 2.1 候选先行，人工确认

系统可以自动生成知识候选，但不能自动生成 confirmed knowledge。

```text
confirmed diagnosis -> knowledge candidate -> human review -> confirmed knowledge
```

这个边界和诊断结论一致：模型和规则只能提出候选，最终确认必须由人完成。

### 2.2 知识必须可追溯

每条知识候选必须能追溯到：

- 原始 `diagnosis_id`；
- 原始 `conclusion_id`；
- 原始 `cited_evidence_ids`；
- 生成来源；
- 审核记录。

不能出现“凭空生成的一条经验”。

### 2.3 只沉淀摘要，不保存敏感 payload

知识候选应该保存可复用的模式，而不是完整现场数据。

允许保存：

- 故障类型；
- 候选标签；
- 根因摘要；
- 适用条件；
- 排查步骤；
- 排除项；
- 证据 ID 引用。

禁止保存：

- 设备真实密码；
- Token / secret；
- 人员标识；
- 真实卡号；
- 真实车牌；
- 图片 / 视频 / 截图 URL；
- 大段原始设备配置 payload。

### 2.4 先轻量，后 RAG

Phase 5 不直接上复杂向量库。优先落地：

```text
领域模型 -> 内存/JSON 候选知识 -> knowledge__search 契约兼容 -> 固定评测
```

后续再演进到：

```text
SQLite -> FTS -> 向量检索 -> BGE Embedding -> Hybrid Search -> 企业知识库
```

## 3. Phase 5A：Knowledge Candidate 领域模型

### 3.1 目标

定义知识候选的领域模型、状态机与人工审核边界。

### 3.2 新增模型

建议新增文件：

```text
src/security_diagnosis_harness/domain/knowledge.py
```

建议模型：

| 模型 | 职责 |
|---|---|
| `KnowledgeCandidate` | 一条可审核的候选知识 |
| `KnowledgeReview` | 对知识候选的人工审核记录 |
| `KnowledgeCandidateStatus` | candidate / confirmed / rejected / retired |
| `KnowledgeCandidateSource` | diagnosis_confirmation / manual_seed / imported |

### 3.3 KnowledgeCandidate 建议字段

| 字段 | 说明 |
|---|---|
| `knowledge_id` | 知识候选 ID |
| `fault_type` | 对应故障类型 |
| `candidate_label` | 候选根因标签 |
| `title` | 短标题 |
| `summary` | 根因摘要 |
| `symptoms` | 典型现象 |
| `root_cause` | 归纳后的根因 |
| `troubleshooting_steps` | 推荐排查步骤 |
| `excluded_causes` | 已排除或不优先考虑的候选 |
| `source` | 生成来源 |
| `source_diagnosis_id` | 原始诊断 ID |
| `source_conclusion_id` | 原始结论 ID |
| `source_evidence_ids` | 原始证据引用 |
| `status` | 当前知识状态 |
| `reviews` | 人工审核记录 |
| `redacted` | 是否发生脱敏 |
| `created_at` / `updated_at` | 时间 |

### 3.4 状态流转

```text
candidate
  -> confirmed
  -> retired

candidate
  -> rejected
```

约束：

- `confirmed` 只能由 `KnowledgeReviewAction.CONFIRM` 产生；
- `rejected` 只能由 `KnowledgeReviewAction.REJECT` 产生；
- `retired` 只能由 `KnowledgeReviewAction.RETIRE` 产生；
- rejected 不能再 confirmed；
- retired 不能再回到 confirmed；
- 模型、规则、自动提取不能直接产生 confirmed knowledge。

### 3.5 Phase 5A 禁止事项

本阶段不实现：

- 数据库；
- API；
- `knowledge__search` 替换；
- RAG；
- 向量库；
- 真实模型；
- 自动从诊断生成知识；
- 自动审核通过。

## 4. Phase 5B：confirmed 诊断生成 Knowledge Candidate

### 4.1 目标

在诊断已经由人工确认后，从 DiagnosisCase 中提取可复用经验，生成 `KnowledgeCandidate`。

### 4.2 建议新增模块

```text
src/security_diagnosis_harness/application/knowledge_candidates.py
```

职责：

- 校验 Diagnosis 必须是 `confirmed`；
- 校验必须有 conclusion；
- 校验 conclusion 必须有 cited_evidence_ids；
- 基于 fault_type / candidate_label / conclusion / troubleshooting_order 生成知识候选；
- 不保存完整 Evidence payload；
- 对文本字段进行脱敏；
- 返回 candidate 状态。

### 4.3 生成内容

| 内容 | 来源 |
|---|---|
| title | fault_type + candidate_label |
| summary | conclusion.summary |
| root_cause | conclusion.root_cause 或 candidate explanation |
| symptoms | case.description + evidence summary |
| troubleshooting_steps | conclusion.next_steps + rule troubleshooting_order |
| excluded_causes | rule excluded_candidates |
| source_evidence_ids | conclusion.cited_evidence_ids |

### 4.4 禁止事项

- 不能从未确认诊断生成知识；
- 不能把 KnowledgeCandidate 状态直接设为 confirmed；
- 不能复制完整 Evidence payload；
- 不能保存未脱敏敏感信息；
- 不能改变原始 Diagnosis / Conclusion。

## 5. Phase 5C：轻量知识检索与 RAG 演进入口

### 5.1 目标

让 `knowledge__search` 可以召回人工确认后的知识，同时保持工具契约不变。

### 5.2 建议实现

新增 KnowledgeRepository Port：

```text
src/security_diagnosis_harness/ports/knowledge_repository.py
```

新增内存或 JSON 适配器：

```text
src/security_diagnosis_harness/adapters/knowledge/in_memory.py
```

改造 `knowledge__search`：

- 输入仍然是 `query`、`limit`；
- 输出仍然是 `knowledge_sop` EvidenceDraft；
- payload 可以增加 `knowledge_candidates`；
- 工具失败仍然不能产生 EvidenceDraft；
- 没有命中时返回空结果，不伪造知识。

### 5.3 RAG 演进接口

Phase 5C 只定义轻量检索，不强制接向量库。

后续企业化可演进为：

```text
KnowledgeRepository Port
  -> SQLite FTS Adapter
  -> Vector Adapter
  -> BGE Embedding Adapter
  -> Hybrid Retriever
```

如果本机已有 BGE Docker 服务，可以作为后续 adapter 接入，但 Phase 5C 不依赖它。

## 6. 与现有代码映射

| 现有模块 | Phase 5 关系 |
|---|---|
| `SecurityDiagnosisCase` | KnowledgeCandidate 的来源 |
| `DiagnosisConclusion` | 知识候选摘要和根因来源 |
| `DiagnosisEvidence` | 只保存 evidence_id 引用，不复制完整 payload |
| `HumanReview` | 诊断 confirmed 的前提 |
| `knowledge__search` | Phase 5C 保持契约，扩展内部来源 |
| `CitationPolicy` | 知识召回可作为辅助，但只引用知识最多 possible |
| Phase 1～4 eval | Phase 5C 必须保证不退化 |

## 7. 总体禁止事项

Phase 5 全阶段禁止：

- 自动把知识候选标记为 confirmed；
- 未经人工确认就进入正式知识库；
- 保存真实密码、Token、secret、卡号、人脸、车牌、截图 URL；
- 引入外部模型自动测试；
- 修改 ToolLoopRunner 主循环；
- 把知识召回当作设备事实支撑 `probable`；
- 破坏 Phase 1～4 固定评测。

## 8. 总体完成定义

Phase 5 完成后，应能演示：

```text
一个固定诊断案例
  -> 运行诊断
  -> 人工 confirm
  -> 生成 KnowledgeCandidate(candidate)
  -> 人工 confirm knowledge
  -> knowledge__search 可召回该知识
  -> 下一次类似诊断报告中出现可追溯知识
```

并且满足：

- 所有测试通过；
- Phase 1～4 eval 不退化；
- 敏感信息泄露为 0；
- confirmed knowledge 只能人工产生；
- 知识可追溯到原始诊断和 Evidence。
