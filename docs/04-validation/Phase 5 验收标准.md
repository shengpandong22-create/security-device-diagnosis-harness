# Phase 5 验收标准

> 验收对象：知识沉淀与 RAG 增强入口。  
> Phase 5 分三段：5A 知识候选领域模型、5B confirmed 诊断生成知识候选、5C 轻量知识检索。  
> 本文档不要求接真实模型、真实设备、数据库或向量库。  

## 1. Phase 5A：Knowledge Candidate 领域模型（待开始）

### 1.1 工程验收

- [ ] `uv run ruff check .` 通过；
- [ ] `uv run pytest` 通过；
- [ ] Phase 0 demo 仍可运行；
- [ ] Phase 1 eval 仍 5/5；
- [ ] Phase 2 eval 仍 5/5；
- [ ] Phase 3 eval 仍 5/5；
- [ ] Phase 4 eval 仍 5/5；
- [ ] 领域模型只依赖标准库与 pydantic；
- [ ] 不新增 API、Tool、Runner、DeviceGateway 改动；
- [ ] 不新增数据库 / SQLAlchemy / Alembic / 真实模型 SDK；
- [ ] 不提交真实设备 IP、账号、密码、Token、邮箱授权码、截图或视频 URL。

### 1.2 知识候选模型验收

- [ ] 新增 `KnowledgeCandidate`；
- [ ] 新增 `KnowledgeReview`；
- [ ] 新增 `KnowledgeCandidateStatus`；
- [ ] 新增 `KnowledgeCandidateSource`；
- [ ] 候选知识必须关联 `fault_type`；
- [ ] 候选知识必须有 `title`、`summary`、`root_cause`、`troubleshooting_steps`；
- [ ] 候选知识必须能追溯 `source_diagnosis_id`、`source_conclusion_id` 和 `source_evidence_ids`；
- [ ] 文本字段有长度上限；
- [ ] `source_evidence_ids` 不允许为空；
- [ ] 敏感字段脱敏为 `***REDACTED***`。

### 1.3 状态与人工审核验收

- [ ] 初始状态只能是 `candidate`；
- [ ] `confirmed` 只能由 `KnowledgeReviewAction.CONFIRM` 产生；
- [ ] `rejected` 只能由 `KnowledgeReviewAction.REJECT` 产生；
- [ ] `retired` 只能由 `KnowledgeReviewAction.RETIRE` 产生；
- [ ] rejected 不能再 confirmed；
- [ ] retired 不能再 confirmed；
- [ ] 模型、规则或自动提取不能直接产生 confirmed knowledge；
- [ ] 审核记录必须保留 reviewer、action、comment、reviewed_at。

### 1.4 Phase 5A 完成状态

| 项目 | 当前结论 |
|---|---|
| 新增领域文件 | 待完成 |
| 新增测试文件 | 待完成 |
| confirmed 边界 | 待完成 |
| 后续衔接 | Phase 5B 基于该领域模型生成候选知识 |

## 2. Phase 5B：confirmed 诊断生成 Knowledge Candidate（待开始）

- [ ] 新增知识候选生成应用服务；
- [ ] 只有 `SecurityDiagnosisStatus.CONFIRMED` 的诊断可以生成候选知识；
- [ ] 没有 conclusion 的诊断不能生成候选知识；
- [ ] conclusion 没有引用 Evidence 时不能生成候选知识；
- [ ] 生成结果必须是 `candidate`；
- [ ] 不复制完整 Evidence payload；
- [ ] 不修改原始 Diagnosis / Conclusion；
- [ ] 可从摄像头、录像、门禁、报警四类 confirmed 诊断生成候选。

## 3. Phase 5C：轻量知识检索与 RAG 演进入口（待开始）

- [ ] 新增 KnowledgeRepository Port；
- [ ] 新增内存或 JSON Knowledge Adapter；
- [ ] `knowledge__search` 工具输入输出契约保持兼容；
- [ ] confirmed knowledge 可被召回；
- [ ] candidate / rejected knowledge 默认不可被诊断召回；
- [ ] 知识召回仍然落成 `knowledge_sop` Evidence；
- [ ] 知识类 Evidence 不能作为设备事实支撑 `probable`；
- [ ] Phase 1～4 eval 不退化。

## 4. Phase 5 固定评测目标

| 指标 | 目标 |
|---|---:|
| Phase 1 label_accuracy | 1.0 |
| Phase 2 label_accuracy | 1.0 |
| Phase 3 label_accuracy | 1.0 |
| Phase 4 label_accuracy | 1.0 |
| citation_compliance | 1.0 |
| sensitive_leak_count | 0 |
| external_model_called | false |

## 5. Phase 5 Definition of Done

- [ ] Phase 5A 领域模型与测试完成；
- [ ] Phase 5B confirmed 诊断生成知识候选完成；
- [ ] Phase 5C 轻量知识检索完成；
- [ ] confirmed knowledge 只能由人工审核产生；
- [ ] 知识可追溯到原始 Diagnosis、Conclusion 和 Evidence；
- [ ] 引入知识后 Phase 1～4 评测不退化；
- [ ] Git 工作区干净并推送。
