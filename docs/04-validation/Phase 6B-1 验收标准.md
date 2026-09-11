# Phase 6B-1 验收标准

> 验收对象：正式本地运行装配（RuntimeSettings + RuntimeContainer）与真实重启恢复。
> 本阶段不接真实设备 / 真实模型 / BGE / 网络，不进入 6B-2 乐观锁。

## 1. 入口边界（必须准确区分）

| 入口 | 用途 | 仓储 | 副作用 |
|---|---|---|---|
| `create_app()` | 应用工厂（纯函数） | 默认内存装配 | 无 |
| 模块级 `api.app.app` | 兼容既有导入与测试 | 内存装配 | 无 |
| `build_container()` | 评测 / demo 装配 | 内存 | 无 |
| `build_phase1~4_container()` | 固定案例评测 | 内存 | 无 |
| `scripts/run_api.py` | **正式本地 SQLite 运行入口** | SQLite | 建目录 / 迁移 / 启动服务 |
| `build_runtime_container(settings)` | 正式运行装配 | 按配置 | 迁移 + Engine |

**禁止**描述为「所有 API 构建方式默认 SQLite」。

## 2. RuntimeSettings（`src/security_diagnosis_harness/config.py`）

- [x] 字段：`repository_mode` / `database_url` / `database_echo` / `auto_migrate`；
- [x] 正式默认：`sqlite` / `sqlite:///./data/security-diagnosis.db` / `false` / `true`；
- [x] 优先级：**显式参数 > 环境变量 > 安全默认值**；
- [x] 环境变量：`SECURITY_DIAGNOSIS_REPOSITORY` / `SECURITY_DIAGNOSIS_DB_URL` /
      `SECURITY_DIAGNOSIS_DB_ECHO` / `SECURITY_DIAGNOSIS_AUTO_MIGRATE`；
- [x] 非法 `RepositoryMode` → `RuntimeConfigurationError`；
- [x] 非法布尔值 → `RuntimeConfigurationError`；
- [x] 非 SQLite URL（PostgreSQL / MySQL / MSSQL / Oracle）→ `RuntimeConfigurationError`；
- [x] 允许本地 SQLite 文件与 `sqlite:///:memory:`；
- [x] 不读取 `.env`；
- [x] `repr` / `str` / 异常信息均不包含完整 `database_url` 或凭证；
- [x] 配置对象不持有 Engine / Session 等基础设施对象。

## 3. RuntimeContainer（`src/security_diagnosis_harness/runtime.py`）

- [x] 持有 `settings` / `service` / `repository` / `engine` / `session_factory` /
      `runner` / `registry` / `gateway` / `llm` / `citation_policy` / `close()`；
- [x] sqlite 模式装配 `SqlAlchemyDiagnosisRepository`；
- [x] memory 模式装配 `InMemoryDiagnosisRepository`（`engine` / `session_factory` 为 `None`）；
- [x] Engine 由 RuntimeContainer 独占；
- [x] `close()` dispose Engine；**可重复调用**；memory 模式安全无副作用；
- [x] 支持 context manager（`__enter__` / `__exit__`）；
- [x] `database_ready()` 反映运行时真实状态（close 后为 `False`）；
- [x] 类型注解使用 `DiagnosisRepository` Port，未固定为内存实现；
- [x] 不把 Session 暴露给 API / Application；
- [x] 不是全局可变单例（两次构建对象互不相同）。

## 4. 程序化 Alembic 迁移

- [x] `upgrade_database(url)` 使用 `alembic.command.upgrade(config, "head")`；
- [x] **不使用** `Base.metadata.create_all()`（有 monkeypatch 断言）；
- [x] Alembic 配置基于源码目录解析，**不依赖调用者 cwd**（有跨目录子进程测试）；
- [x] URL 通过 `Config.set_main_option` 注入，不修改全局 `alembic.ini`；
- [x] 迁移失败时向上抛出，`RuntimeContainer` 构建失败并 dispose 已建 Engine；
- [x] `auto_migrate=false` 不执行迁移；表不存在时首次 Repository 操作
      **受控失败**（`RepositoryPersistenceError`），不偷偷建表；
- [x] `path_separator = os`（替代弃用的 `version_path_separator`），
      弃用警告消失且 upgrade/downgrade 行为不变。

## 5. 真实重启恢复

- [x] 使用**文件型**临时 SQLite（非 `:memory:`），非 pickle / JSON 绕过；
- [x] A 在创建 B 之前已 `close()`；
- [x] B 的 Engine / SessionFactory / Repository / Service 与 A **均非同一对象**；
- [x] 唯一共享状态是磁盘上的 SQLite 文件；
- [x] 恢复 `diagnosis_id` / `status=confirmed` / Evidence 及 ID /
      Conclusion 及 ID / `cited_evidence_ids` / HumanReview ID /
      `created_at` / `updated_at`（aware）/ Evidence `content_hash`；
- [x] `close()` 后临时数据库可删除，无连接泄漏。

## 6. API Health 与错误映射

- [x] `HealthData` 新增 `repository_mode` / `database_ready`，并提供兼容默认值；
- [x] `phase` = `"6B"`；
- [x] `database_ready` 不是硬编码（close 后返回 `false`）；
- [x] health 不返回 database_url / 绝对路径 / Engine / Session / 用户目录 / 凭证。

错误映射：

| 异常 | 状态码 | code |
|---|---|---|
| `DiagnosisNotFoundError` | 404 | `diagnosis_not_found` |
| `KnowledgeNotFoundError` | 404 | `knowledge_not_found` |
| `DiagnosisAlreadyExistsError` | 409 | `diagnosis_already_exists` |
| `KnowledgeAlreadyExistsError` | 409 | `knowledge_already_exists` |
| `CitationPolicyViolation` | 422 | `citation_policy_violation` |
| `InvalidStatusTransition` | 409 | `invalid_status_transition` |
| `ReviewNotAllowed` | 409 | `review_not_allowed` |
| `RepositoryPersistenceError` | 503 | `repository_unavailable` |

- [x] 503 只返回安全文案 `诊断数据暂时不可用`；
- [x] 503 响应体不含 `OperationalError` / `IntegrityError` / SQL / URL /
      文件路径 / traceback / ORM 类名（有 body 级断言）。

## 7. 测试真实性证据

| 结论 | 证明方式 |
|---|---|
| 真正重启恢复 | A/B 对象身份对比 + close 顺序 + 字段逐项比对 |
| import 无副作用 | 独立子进程 + 文件系统快照（`scripts/probe_import_side_effects.py`） |
| 迁移失败阻止启动 | 注入可控 Alembic 异常，断言不返回容器 |
| 未用 create_all | monkeypatch `MetaData.create_all` 断言调用次数为 0 |
| Engine 已释放 | 断言 `engine is None` + 临时数据库文件可删除 |

## 8. 验收命令与结果

```text
uv run ruff check .                                 -> All checks passed!
uv run pytest                                       -> 927 passed
git diff --check                                    -> 通过
uv run python scripts/demo_phase6_persistence_restart.py
uv run python scripts/demo_phase0_camera_black_screen.py
uv run python scripts/eval_phase1_camera_black_screen.py
uv run python scripts/eval_phase2_recording_missing.py
uv run python scripts/eval_phase3_access_card_failed.py
uv run python scripts/eval_phase4_alarm_false_positive.py
（Phase 5 用 FakeEmbeddingAdapter 离线执行，不访问 BGE）
```

重启 Demo 输出：

```json
{
  "repository_mode": "sqlite",
  "restart_recovered": true,
  "new_engine_created": true,
  "new_session_factory_created": true,
  "new_repository_created": true,
  "new_service_created": true,
  "status": "confirmed",
  "evidence_count": 4,
  "evidence_ids_preserved": true,
  "conclusion_id_preserved": true,
  "citations_preserved": true,
  "review_id_preserved": true,
  "timestamps_are_aware": true,
  "database_removed_after_close": true,
  "external_model_called": false
}
```

import 副作用探针输出：

```json
{
  "import_exit_code": 0,
  "files_created": [],
  "database_files_created": [],
  "data_directory_created": false
}
```

## 9. 收尾修复（跨域护栏与资源生命周期）

### 9.1 P0：跨故障域结论

真实缺陷：正式 RuntimeContainer 只装配摄像头工具与摄像头 responder，但
`create_diagnosis` 接受所有 `SecurityFaultType`，且 `set_conclusion()` 只校验
`diagnosis_id` 不校验 `fault_type`，导致录像 Case 可以拿到摄像头结论并进入
`waiting_for_confirmation`。

修复：

- [x] 新增领域异常 `ConclusionFaultTypeMismatch`（继承 `DomainError`）；
- [x] `SecurityDiagnosisCase.set_conclusion()` 强制
      `conclusion.fault_type == case.fault_type`，且**在任何状态变更之前**校验；
- [x] `CitationPolicy.validate()` 增加同口径的防御性校验
      （防止调用方绕过 Case 方法直接调用 Policy）；
- [x] 拒绝时 `case.conclusion` / `status` / `updated_at` 均不发生改变；
- [x] 错误结论无法进入 `waiting_for_confirmation`，也无法被人工 confirm。

### 9.2 P0：正式 Runtime 能力边界

- [x] `SUPPORTED_RUNTIME_FAULT_TYPES = {CAMERA_BLACK_SCREEN}`，
      由 `supported_runtime_fault_types()` 暴露；
- [x] `SecurityDiagnosisApplicationService` 新增可选
      `supported_fault_types: frozenset[SecurityFaultType] | None = None`
      （`None` = 不限制，Phase 0～5 评测 Container 语义不变）；
- [x] `create_diagnosis` 能力闸门在**写库之前**执行；
- [x] `run_diagnosis` 能力闸门在**状态推进之前**再次执行
      （防数据库历史遗留的不支持类型）；
- [x] 拒绝时抛 `UnsupportedFaultTypeError`，不写入 Evidence / Conclusion / Review，
      不返回 `ok=true`；
- [x] API 映射 `UnsupportedFaultTypeError → 422`，code = `unsupported_fault_type`；
- [x] Phase 1～4 独立评测 Container 行为不变（不限制故障类型）；
- [x] 未通过扩大 allowlist 假装支持四域。

### 9.3 P1：资源生命周期

- [x] 删除 `build_runtime_service()`（会创建 Engine 却只返回 service，丢失 owner）；
- [x] `runtime.py` 不存在会丢失 Engine owner 的 builder（有 AST 守卫测试）；
- [x] `migrations/env.py` 的迁移 Engine 在 `try / finally` 中
      `connectable.dispose()`（成功与失败路径都释放）；
- [x] `scripts/run_api.py` 的 `build_app()` 在 `create_app` 失败时
      先 `runtime.close()` 再向上抛；
- [x] `main()` 在 `finally` 中 `runtime.close()`。

### 9.4 health 语义（准确表述）

`database_ready` 的准确含义是：

> **Runtime 数据库组件已成功初始化且 Container 未关闭。**

它**不**代表：数据库实时可连接、数据库文件仍存在、SQL 查询一定成功，
也不是完整的 readiness probe。实时数据库探活留给后续阶段。

### 9.5 真实跨域探针

```text
RECORDING_CASE_ACCEPTED_BY_CAMERA_RUNTIME: False
CROSS_FAULT_CONCLUSION_ACCEPTED: False
CITATION_POLICY_ACCEPTED_CROSS_FAULT: False
CASE_STATE_MUTATED_AFTER_REJECTION: False
```

（`scripts/probe_cross_fault_guard.py`，真实 SQLite Runtime 执行）

## 10. 明确未实现（留给后续阶段）

- Phase 6B-2 乐观锁 / `version` 字段 / Unit of Work / 多线程并发更新；
- Knowledge 管理 API；
- 默认运行时 Hybrid Retriever / BGE；
- Phase 6C 审计、备份与恢复；
- PostgreSQL / pgvector / Redis / 消息队列 / 前端 / 主应用 Docker 化；
- 真实设备 Gateway、真实 LLM、外部 HTTP。
