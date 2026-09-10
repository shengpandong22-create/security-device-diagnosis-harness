# Phase 5 验收标准

> 验收对象：知识沉淀与 RAG 增强入口。  
> Phase 5 分三段：5A 知识候选领域模型、5B confirmed 诊断生成知识候选、5C 轻量知识检索。  
> 本文档不要求接真实模型、真实设备、数据库或向量库。  

## 1. Phase 5A：Knowledge Candidate 领域模型（已完成）

### 1.1 工程验收

- [x] `uv run ruff check .` 通过；
- [x] `uv run pytest` 通过；
- [x] Phase 0 demo 仍可运行；
- [x] Phase 1 eval 仍 5/5；
- [x] Phase 2 eval 仍 5/5；
- [x] Phase 3 eval 仍 5/5；
- [x] Phase 4 eval 仍 5/5；
- [x] 领域模型只依赖标准库与 pydantic；
- [x] 不新增 API、Tool、Runner、DeviceGateway 改动；
- [x] 不新增数据库 / SQLAlchemy / Alembic / 真实模型 SDK；
- [x] 不提交真实设备 IP、账号、密码、Token、邮箱授权码、截图或视频 URL。

### 1.2 知识候选模型验收

- [x] 新增 `KnowledgeCandidate`；
- [x] 新增 `KnowledgeReview`；
- [x] 新增 `KnowledgeCandidateStatus`；
- [x] 新增 `KnowledgeCandidateSource`；
- [x] 候选知识必须关联 `fault_type`；
- [x] 候选知识必须有 `title`、`summary`、`root_cause`、`troubleshooting_steps`；
- [x] 候选知识必须能追溯 `source_diagnosis_id`、`source_conclusion_id` 和 `source_evidence_ids`；
- [x] 文本字段有长度上限；
- [x] `source_evidence_ids` 不允许为空；
- [x] 敏感字段脱敏为 `***REDACTED***`。

### 1.3 状态与人工审核验收

- [x] 初始状态只能是 `candidate`；
- [x] `confirmed` 只能由 `KnowledgeReviewAction.CONFIRM` 产生；
- [x] `rejected` 只能由 `KnowledgeReviewAction.REJECT` 产生；
- [x] `retired` 只能由 `KnowledgeReviewAction.RETIRE` 产生；
- [x] rejected 不能再 confirmed；
- [x] retired 不能再 confirmed；
- [x] 模型、规则或自动提取不能直接产生 confirmed knowledge；
- [x] 审核记录必须保留 reviewer、action、comment、reviewed_at。

### 1.4 Phase 5A 完成状态

| 项目 | 当前结论 |
|---|---|
| 新增领域文件 | `src/security_diagnosis_harness/domain/knowledge.py` |
| 新增测试文件 | `tests/domain/test_knowledge.py` |
| confirmed 边界 | 初始只能 candidate，confirmed 只能由 `KnowledgeReviewAction.CONFIRM` 产生 |
| 后续衔接 | Phase 5B 基于该领域模型生成候选知识 |

## 2. Phase 5B：confirmed 诊断生成 Knowledge Candidate（已完成）

- [x] 新增知识候选生成应用服务；
- [x] 只有 `SecurityDiagnosisStatus.CONFIRMED` 的诊断可以生成候选知识；
- [x] confirmed 状态必须同时存在人工 confirm 审核记录，伪造状态不能生成知识；
- [x] 没有 conclusion 的诊断不能生成候选知识；
- [x] conclusion 没有引用 Evidence 时不能生成候选知识；
- [x] conclusion 引用了不存在的 Evidence 时不能生成候选知识；
- [x] 生成结果必须是 `candidate`；
- [x] 不复制完整 Evidence payload；
- [x] 不修改原始 Diagnosis / Conclusion；
- [x] 文本字段在知识领域模型入口统一脱敏；
- [x] 可从摄像头、录像、门禁、报警四类 confirmed 诊断生成候选；
- [x] 四类诊断均经过 Runner、工具、Evidence、CitationPolicy 和人工确认的完整本地链路验收。

### 2.1 Phase 5B 完成状态

| 项目 | 当前结论 |
|---|---|
| 新增应用文件 | `src/security_diagnosis_harness/application/knowledge_candidates.py` |
| 新增测试文件 | `tests/application/test_knowledge_candidates.py` |
| 输入闸门 | confirmed 状态 + 人工 confirm 记录 + conclusion + 有效 Evidence 引用 |
| 输出边界 | 只生成 candidate，不持久化、不自动确认 |
| 数据最小化 | 复制摘要和来源 ID，不复制 Evidence payload |
| 四域闭环 | 摄像头、录像、门禁、报警均通过真实本地应用链路测试 |

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

- [x] Phase 5A 领域模型与测试完成；
- [x] Phase 5B confirmed 诊断生成知识候选完成；
- [ ] Phase 5C 轻量知识检索完成；
- [x] confirmed knowledge 只能由人工审核产生；
- [x] 知识可追溯到原始 Diagnosis、Conclusion 和 Evidence；
- [ ] 引入知识后 Phase 1～4 评测不退化；
- [ ] Git 工作区干净并推送。
