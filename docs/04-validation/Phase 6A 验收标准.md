# Phase 6A 验收标准

> 验收对象：SQLite 持久化基座（Diagnosis / Knowledge Repository Adapter + Alembic 迁移）。
> 本文档不接真实设备、不接真实模型、不新增前端、不接外部网络。

## 1. 工程验收

- [x] `uv sync` 成功，新增依赖仅 SQLAlchemy 2.x 与 Alembic；
- [x] `uv run ruff check .` 通过；
- [x] `uv run pytest` 通过（771 个测试，基线 729 项）；
- [x] `git diff --check` 通过；
- [x] 不新增异步数据库驱动（使用同步 SQLite）；
- [x] 不新增 API、不修改 ToolLoopRunner 主循环、不修改 CitationPolicy 可信度规则；
- [x] 不读取 `.env`，不提交密钥 / Token / 真实设备凭证 / 数据库文件。

## 2. 边界验收

- [x] `domain/`、`api/`、`ports/` 中不存在 `sqlalchemy` / `alembic` 导入（有 AST 守卫测试）；
- [x] API Schema 中不存在 ORM 类型；
- [x] 应用服务 `SecurityDiagnosisApplicationService` 依赖 `DiagnosisRepository` Port，不依赖 ORM；
- [x] 诊断仓储 Port 落在 `ports/diagnosis_repository.py`，`InMemoryDiagnosisRepository` 保留；
- [x] `InMemoryKnowledgeRepository` 保留，`knowledge__search` 契约不变；
- [x] `EmbeddingPort` / `HttpBgeEmbeddingAdapter` / `HybridKnowledgeRetriever` 不变；
- [x] 默认 API Container 仍装配内存实现（切换留待 Phase 6B）。

## 3. 数据表验收

### 3.1 `diagnosis_cases`

| 列 | 类型 | 说明 |
|---|---|---|
| `diagnosis_id` | String(64) PK | 诊断 ID |
| `fault_type` | String(64) 索引 | 故障类型枚举值 |
| `device_id` | String(128) 索引 | 设备 ID |
| `reporter` | String(128) | 上报人 |
| `description` | Text | 描述 |
| `status` | String(32) 索引 | 诊断状态枚举值 |
| `created_at` / `updated_at` | DateTime(tz) | 创建 / 更新时间 |
| `evidence` | JSON | Evidence 列表（含 ID / 类型 / payload / hash） |
| `conclusion` | JSON NULL | 候选结论（含 `cited_evidence_ids`） |
| `reviews` | JSON | 人工审核记录 |

### 3.2 `knowledge_candidates`

| 列 | 类型 | 说明 |
|---|---|---|
| `knowledge_id` | String(64) PK | 知识 ID |
| `fault_type` | String(64) 索引 | 故障类型枚举值 |
| `candidate_label` / `title` / `summary` / `root_cause` | String/Text | 知识正文 |
| `status` | String(32) 索引 | candidate / confirmed / rejected / retired |
| `source_diagnosis_id` | String(64) 索引 | 来源诊断 |
| `source_conclusion_id` | String(64) | 来源结论 |
| `source_evidence_ids` | JSON | 来源 Evidence ID 列表 |
| `symptoms` / `troubleshooting_steps` / `excluded_causes` | JSON | 结构化正文 |
| `reviews` | JSON | 知识审核记录 |
| `source` / `redacted` / `metadata` | String/Bool/JSON | 来源、脱敏标记、扩展元数据 |
| `created_at` / `updated_at` | DateTime(tz) | 创建 / 更新时间 |

## 4. Domain ↔ ORM 转换验收

- [x] 读回时用 `model_validate` 重新构造真正的 Domain 对象，不返回 ORM 行；
- [x] 枚举（fault_type / status / evidence_type / confidence / action）恢复为枚举成员；
- [x] 时间字段统一为 timezone-aware UTC；
- [x] Evidence ID 与 Conclusion 引用关系不丢失；
- [x] HumanReview / KnowledgeReview 记录不丢失；
- [x] 保存前后领域语义一致（往返相等）。

## 5. Repository 行为验收

| 行为 | 内存实现 | SQLite 实现 |
|---|---|---|
| `save()` 重复 ID | `DiagnosisAlreadyExistsError` | `DiagnosisAlreadyExistsError` |
| `update()` 不存在 ID | `DiagnosisNotFoundError` | `DiagnosisNotFoundError` |
| `get()` 不存在 ID | `DiagnosisNotFoundError` | `DiagnosisNotFoundError` |
| 知识 `save()` 重复 ID | `ValueError` | `ValueError` |
| 知识 `get/update` 不存在 | `KnowledgeNotFoundError` | `KnowledgeNotFoundError` |
| 读后本地修改 | 不隐式落库 | 不隐式落库 |

- [x] 不依赖底层 SQLAlchemy 异常作为公开契约；
- [x] 所有读操作返回独立 Domain 对象（深拷贝语义）。

## 6. 知识状态治理验收

- [x] `candidate` 可保存但不能被 `search_confirmed` 召回；
- [x] `confirmed` 可召回；
- [x] `rejected` / `retired` 不能召回；
- [x] `fault_type` 不匹配不能召回；
- [x] 检索只返回 `status == confirmed` 且 `fault_type` 匹配的候选。

## 7. Alembic 迁移验收

针对临时数据库执行（不污染仓库）：

- [x] `alembic upgrade head` 建出 `diagnosis_cases` / `knowledge_candidates` / `alembic_version`；
- [x] `alembic downgrade base` 移除业务表；
- [x] 再次 `alembic upgrade head` 成功；
- [x] 迁移表结构与 ORM metadata 列集合一致；
- [x] 迁移不依赖开发机绝对路径；
- [x] 默认 URL 为相对路径示例 `sqlite:///./data/security-diagnosis.db`；
- [x] 临时数据库测试后可删除，无连接泄漏。

## 8. 安全验收

- [x] 明文 password / Token / secret 不写入数据库（Domain 脱敏 + Adapter 边界检查）；
- [x] 不形成两套互相矛盾的脱敏规则（复用 Domain 脱敏逻辑）；
- [x] `.gitignore` 覆盖 `*.db` / `*.sqlite` / `*.sqlite3` 及 `-wal` / `-shm` / `-journal`；
- [x] 自动测试不访问真实网络 / BGE / LLM / 设备。

## 9. 回归验收

- [x] Phase 0 demo 通过；
- [x] Phase 1 摄像头黑屏评测 5/5；
- [x] Phase 2 录像缺失评测 5/5；
- [x] Phase 3 门禁异常评测 5/5；
- [x] Phase 4 报警误报评测 5/5；
- [x] Phase 5 检索评测可离线跑通（`FakeEmbeddingAdapter`，不依赖 BGE 服务）。
