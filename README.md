# Security Device Diagnosis Harness

> 面向安防设备运维场景的可信诊断 Agent Harness。

本项目不是把服务日志丢给大模型生成答案，而是把 LLM 放入一个受控诊断 Harness：由确定性工程层负责设备事实采集、工具权限、Evidence 留存、引用校验、人工确认和评测回归，LLM 只在这些边界内完成推理。

## 项目定位

- 业务域：安防设备运维诊断，优先覆盖摄像头黑屏、录像缺失、门禁刷卡异常、报警误报等场景。
- 技术目标：验证 Agent 如何在设备状态、告警事件、配置快照、知识库 SOP 和人工反馈之间形成可信闭环。
- 当前阶段：Phase 0～6 已完成；Phase 7A～7C 数据集、Grader 与版本门禁已完成，Phase 7D 待实施。
- 重要边界：本项目不继承应用日志诊断主线，不迁移 Java Lab、NPE、服务日志、源码诊断、Gateway/Nacos/Trace 作为主叙事。

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0A | 项目骨架与领域模型 | 已完成 |
| Phase 0B | Harness 与只读工具 | 已完成 |
| Phase 0C | API、报告与 demo | 已完成 |
| Phase 1 | 摄像头黑屏深化（多子场景 + 评测） | 已完成 |
| Phase 2A | 录像缺失 / 录像异常领域模型 | 已完成 |
| Phase 2B | 录像只读工具与样例案例 | 已完成 |
| Phase 2C | 录像规则推断、报告增强与评测 | 已完成 |
| Phase 3A | 门禁领域模型 | 已完成 |
| Phase 3B | 门禁只读工具与样例案例 | 已完成 |
| Phase 3C | 门禁规则、报告与固定评测 | 已完成 |
| Phase 4A | 报警误报领域模型 | 已完成 |
| Phase 4B | 报警只读工具与样例案例 | 已完成 |
| Phase 4C | 报警规则、报告与固定评测 | 已完成 |
| Phase 5A | 知识候选领域模型 | 已完成 |
| Phase 5B | confirmed 诊断生成知识候选 | 已完成 |
| Phase 5C | 知识治理、BGE 与混合检索评测 | 已完成 |
| Phase 6A | SQLite 持久化基座（Repository 与迁移） | 已完成 |
| Phase 6B-1 | 正式运行装配（SQLite）与真实重启恢复 | 已完成 |
| Phase 6B-2 | 乐观锁与并发状态更新保护 | 已完成 |
| Phase 6C | 审计、一致性与备份恢复验收 | 已完成 |
| Phase 7 | 分层数据集、Grader 与版本回归门禁 | 7A～7C 已完成 |

Phase 6C 提供追加式审计、只读一致性扫描与 SQLite 安全备份恢复：

```powershell
uv run python scripts/check_phase6_consistency.py
uv run python scripts/demo_phase6_backup_restore.py
```

恢复操作只允许在持有目标数据库的 `RuntimeContainer` 已关闭后执行；备份恢复
是显式运维动作，不暴露为普通诊断 API。

Phase 0A 交付范围：

- Python 3.12 + uv + pytest + ruff 工程骨架；
- 最小 FastAPI 应用与 `GET /health`；
- 安防设备诊断领域模型与状态机；
- 领域模型测试与最小 API 测试。

Phase 0B 交付范围：

- `ports/llm.py`：LLMClient Port（`ChatMessage` / `ToolCall` / `LLMRequest` / `LLMResponse`）；
- `ports/device_gateway.py`：DeviceGateway Port（只读三件套）；
- `adapters/llm/fake.py`：FakeLLM，按预设回放，不访问网络；
- `adapters/device_gateway/static.py`：StaticDeviceGateway，只读本地 JSON；
- `tools/`：工具契约、Tool Registry 与四个 READ_ONLY 工具；
- `agent/runner.py`：最小受控 ToolLoopRunner（带轮次/工具调用预算）；
- `domain/citation_policy.py`：最小 Citation Policy。

Phase 0C 交付范围：

- `application/`：内存仓储、`SecurityDiagnosisApplicationService`、Markdown 报告渲染；
- `bootstrap/container.py`：装配 StaticDeviceGateway + FakeLLM + 四个只读工具 + Runner + 应用服务；
- `api/routes/diagnoses.py`：诊断创建/查询/运行/证据/审核/报告六个端点；
- `scripts/demo_phase0_camera_black_screen.py`：摄像头黑屏一键 demo。

Phase 1 交付范围：

- `domain/camera.py`：`ChannelSnapshot`、`StreamSnapshot`、`PlatformPullStatus`
  与 `ChannelStatus` / `StreamKind` / `PullStatus` 枚举；
- `samples/devices/camera_black_screen_cases.json`：5 个摄像头黑屏子案例；
- DeviceGateway 与 StaticDeviceGateway 扩展：
  `query_channel_snapshot` / `query_stream_snapshot` / `query_platform_pull_status`；
- 新增三个 READ_ONLY 工具：`device__query_channel`、`device__query_stream`、
  `platform__query_pull_status`；
- `application/camera_diagnosis_rules.py`：候选根因规则（只输出候选，不产生 confirmed）；
- 报告增加候选根因标签、证据链解释、排查顺序与排除项；
- `scripts/eval_phase1_camera_black_screen.py`：固定案例集评测。

### 摄像头黑屏子场景

| case_id | device_id | 事实组合 | candidate_label |
|---|---|---|---|
| `camera_offline` | `cam-offline-01` | 设备离线 | `device_offline_or_network_unreachable` |
| `channel_offline` | `cam-channel-offline-01` | 设备在线、通道离线 | `channel_binding_or_platform_access_issue` |
| `stream_publish_failed` | `cam-stream-failed-01` | 通道在线、码流发布失败 | `stream_publish_or_encoder_issue` |
| `high_bitrate_encoder_timeout` | `cam-high-bitrate-01` | 高码率/高分辨率 + 编码超时 | `overloaded_encoding_configuration` |
| `platform_pull_failed` | `cam-platform-pull-01` | 设备侧正常、平台拉流失败 | `platform_pull_or_access_path_issue` |

Phase 1 评测：

```bash
uv run python scripts/eval_phase1_camera_black_screen.py
```

输出 `demo-output/phase1-camera-black-screen-eval.json` 与 `.md`，
当前 5 个案例 label 命中率 100%、引用合规率 100%、敏感信息泄露 0、
`external_model_called=false`。

Phase 2 交付范围（录像缺失 / 录像异常深化）：

- 2A `domain/recording.py`：`RecordingPlanSnapshot`、`RecordingTimeRange`、
  `StorageSnapshot`、`PlaybackCheckResult` 与 `RecordingPlanStatus` /
  `RecordingMode` / `StorageStatus` / `PlaybackStatus` 枚举；
- 2B DeviceGateway 与 StaticDeviceGateway 新增只读方法：
  `query_recording_plan` / `query_storage_status` / `check_recording_playback`；
- 2B 新增 EvidenceType：`recording_plan`、`storage_status`、`playback_check`；
- 2B 新增三个 READ_ONLY 工具：`recording__query_plan`、`storage__query_status`、
  `recording__check_playback`（均要求 `device:read`）；
- 2B `samples/devices/recording_missing_cases.json`：5 个固定案例
  （计划未启用 / 计划时间空隙 / 存储满 / 存储离线 / 回放索引缺失）；
- 2C `domain/citation_policy.py`：将 `recording_plan` / `storage_status` /
  `playback_check` 纳入设备事实类型，使录像事实也能支撑 `probable`；
- 2C `application/recording_diagnosis_rules.py`：候选根因规则（只输出候选，不产生 confirmed）；
- 2C 应用服务运行诊断后可输出录像类 `candidate_label`（`RunDiagnosisResult`
  的 `candidate_label` 兼容摄像头与录像两类标签）；
- 2C 报告增加录像候选根因、证据链、排查顺序、排除项，以及录像计划 / 存储 / 回放摘要；
- 2C `scripts/eval_phase2_recording_missing.py`：固定案例集评测。

### 录像缺失子场景

| case_id | device_id | 事实组合 | candidate_label |
|---|---|---|---|
| `recording_plan_disabled` | `front-door-cam-1` | 录像计划禁用、回放缺失 | `recording_plan_disabled` |
| `recording_schedule_gap` | `lobby-cam-1` | 计划启用但查询时段不在计划内、回放缺失 | `recording_schedule_gap` |
| `storage_full` | `corridor-cam-1` | 计划正常、存储满、回放缺失 | `storage_capacity_or_pool_issue` |
| `storage_offline` | `corridor-cam-1` | 计划正常、存储离线、回放缺失 | `storage_capacity_or_pool_issue` |
| `playback_index_missing` | `corridor-cam-1` | 计划与存储正常、回放索引缺失 | `playback_index_or_file_issue` |

录像缺失评测：

```bash
uv run python scripts/eval_phase2_recording_missing.py
```

输出 `demo-output/phase2-recording-missing-eval.json` 与 `.md`，
当前 5 个案例 label 命中率 100%、引用合规率 100%、敏感信息泄露 0、
`external_model_called=false`。

Phase 3A 交付范围（门禁刷卡异常领域模型）：

- `domain/access.py`：门禁控制器、门状态、凭证状态、授权策略、刷卡事件五类事实模型；
- 支持控制器离线 / 异常、门锁卡死、凭证冻结或过期、无门权限、授权时段跨天、刷卡拒绝、控制器超时等事实表达；
- `AccessTimeRange` 支持跨天授权与全天授权；
- 卡号、人脸特征、指纹模板、PIN、手机号、身份证、Token、password、secret、credential 等敏感字段入模型前脱敏为 `***REDACTED***`；
- 模型只表达事实，不包含 `confidence`、`root_cause`、`conclusion`、`final_status` 等诊断结论字段；
- 仅新增 Domain 与测试，未接入 Tool、DeviceGateway、Runner、API、数据库、RAG、真实模型或真实设备。

Phase 3B 交付范围（门禁只读工具与样例案例）：

- DeviceGateway 新增门禁只读方法：`query_access_controller`、`query_door`、
  `query_credential`、`query_access_policy`、`search_access_events`；
- StaticDeviceGateway 支持 `access_controller`、`doors`、`credentials`、
  `access_policies`、`access_events` 静态门禁事实；
- 新增 EvidenceType：`access_controller`、`access_door`、`access_credential`、
  `access_policy`、`access_event`；
- 新增五个 READ_ONLY 工具：`access__query_controller`、`access__query_door`、
  `access__query_credential`、`access__query_policy`、`access__search_events`
  （均要求 `device:read`）；
- `samples/devices/access_card_failed_cases.json`：5 个固定案例
  （凭证冻结 / 无门权限 / 不在授权时段 / 控制器离线 / 门锁卡滞）；
- 本阶段仍不修改 CitationPolicy，不做规则推断、报告增强或固定评测，这些留给 Phase 3C。

Phase 3C 交付范围（门禁规则、报告与固定评测）：

- `domain/citation_policy.py`：将 `access_controller` / `access_door` /
  `access_credential` / `access_policy` / `access_event` 纳入设备事实类型；
- `application/access_diagnosis_rules.py`：门禁候选根因规则（只基于 Evidence，不读样例 JSON，不产生 confirmed）；
- `application/diagnoses.py`：运行诊断后可根据 `access_card_failed` 输出门禁类 `candidate_label`；
- `application/reports.py`：报告增加门禁候选根因、证据链、排查顺序、排除项，以及控制器 / 门锁 / 凭证 / 权限 / 刷卡事件摘要；
- `bootstrap/container.py`：新增 Phase 3 本地评测装配，仍使用 `FakeLLM` 与 `StaticDeviceGateway`；
- `scripts/eval_phase3_access_card_failed.py`：固定案例集评测。

### 门禁刷卡异常子场景

| case_id | device_id | 事实组合 | candidate_label |
|---|---|---|---|
| `credential_frozen` | `access-credential-frozen-01` | 凭证冻结、刷卡被拒 | `credential_invalid_or_frozen` |
| `permission_denied` | `access-permission-denied-01` | 凭证有效但无目标门权限 | `permission_not_granted` |
| `time_window_denied` | `access-time-window-denied-01` | 有权限但刷卡时间不在授权时段 | `access_time_window_denied` |
| `controller_offline` | `access-controller-offline-01` | 控制器离线 / 请求超时 | `controller_offline_or_no_response` |
| `door_lock_jammed` | `access-door-lock-jammed-01` | 门锁卡滞 / 门锁反馈异常 | `door_lock_or_sensor_issue` |

门禁刷卡异常评测：

```bash
uv run python scripts/eval_phase3_access_card_failed.py
```

输出 `demo-output/phase3-access-card-failed-eval.json` 与 `.md`，
当前 5 个案例 label 命中率 100%、引用合规率 100%、敏感信息泄露 0、
`external_model_called=false`。

Phase 4A 交付范围（报警误报领域模型）：

- `domain/alarm.py`：报警规则、触发信号、环境干扰、复核结果、重复/关联告警五类事实模型；
- 支持规则灵敏度过高、阈值过低、防抖过短、信号噪声、传感器卡死、雨雾强光风夜间阴影干扰、复核未发现目标、重复告警风暴等事实表达；
- `AlarmTimeRange` 支持跨天布防与全天布防；
- 摄像头画面 URL、视频 URL、人员标识、卡号、车牌、手机号、Token、password、secret 等敏感字段脱敏为 `***REDACTED***`；
- 模型只表达事实，不包含 `confidence`、`root_cause`、`conclusion`、`final_status` 等诊断结论字段；
- 仅新增 Domain 与测试，未接入 Tool、DeviceGateway、Runner、API、数据库、RAG、真实模型或真实设备。

Phase 4B 交付范围（报警只读工具与样例案例）：

- DeviceGateway 新增报警只读方法：`query_alarm_rule`、`query_alarm_signal`、
  `query_alarm_environment`、`query_alarm_verification`、`query_alarm_correlation`；
- StaticDeviceGateway 支持 `alarm_rules`、`alarm_signals`、`alarm_environments`、
  `alarm_verifications`、`alarm_correlations` 静态报警事实；
- 新增 EvidenceType：`alarm_rule`、`alarm_signal`、`alarm_environment`、
  `alarm_verification`、`alarm_correlation`；
- 新增五个 READ_ONLY 工具：`alarm__query_rule`、`alarm__query_signal`、
  `alarm__query_environment`、`alarm__query_verification`、`alarm__query_correlation`
  （均要求 `device:read`）；
- `samples/devices/alarm_false_positive_cases.json`：5 个固定案例
  （规则过敏 / 环境干扰 / 传感器噪声 / 复核未发现目标 / 重复告警风暴）；
- 本阶段仍不修改 CitationPolicy，不做规则推断、报告增强或固定评测，这些留给 Phase 4C。

Phase 4C 交付范围（报警规则、报告与固定评测）：

- `domain/citation_policy.py`：将 `alarm_rule` / `alarm_signal` /
  `alarm_environment` / `alarm_verification` / `alarm_correlation` 纳入设备事实类型；
- `application/alarm_diagnosis_rules.py`：报警误报候选根因规则（只基于 Evidence，不读样例 JSON，不产生 confirmed）；
- `application/diagnoses.py`：运行诊断后可根据 `alarm_false_positive` 输出报警类 `candidate_label`；
- `application/reports.py`：报告增加报警候选根因、证据链、排查顺序、排除项，以及报警规则 / 触发信号 / 环境干扰 / 复核结果 / 关联告警摘要；
- `bootstrap/container.py`：新增 Phase 4 本地评测装配，仍使用 `FakeLLM` 与 `StaticDeviceGateway`；
- `scripts/eval_phase4_alarm_false_positive.py`：固定案例集评测。

### 报警误报子场景

| case_id | device_id | 事实组合 | candidate_label |
|---|---|---|---|
| `rule_too_sensitive` | `alarm-rule-sensitive-01` | 灵敏度高、阈值低、防抖短 | `alarm_rule_too_sensitive` |
| `environment_interference` | `alarm-environment-rain-01` | 雨水与强光干扰 | `environment_interference` |
| `sensor_noise` | `alarm-sensor-noise-01` | 传感器噪声过高 | `sensor_noise_or_stuck` |
| `verification_negative` | `alarm-verification-negative-01` | 复核未发现真实目标 | `verification_negative_false_alarm` |
| `duplicate_alarm_burst` | `alarm-duplicate-burst-01` | 短时间重复告警风暴 | `duplicate_alarm_burst` |

报警误报评测：

```bash
uv run python scripts/eval_phase4_alarm_false_positive.py
```

输出 `demo-output/phase4-alarm-false-positive-eval.json` 与 `.md`，
当前 5 个案例 label 命中率 100%、引用合规率 100%、敏感信息泄露 0、
`external_model_called=false`。

Phase 5A 交付范围（知识候选领域模型）：

- `domain/knowledge.py`：`KnowledgeCandidate`、`KnowledgeReview`、
  `KnowledgeCandidateStatus`、`KnowledgeCandidateSource`、`KnowledgeReviewAction`；
- 候选知识必须关联故障类型、候选标签、标题、摘要、根因、现象、排查步骤；
- 候选知识必须可追溯到 `source_diagnosis_id`、`source_conclusion_id`
  和 `source_evidence_ids`；
- 自动创建时只能是 `candidate`，`confirmed` 只能由人工知识审核产生；
- rejected / retired 不允许重新 confirmed；
- 文本和 metadata 中的密码、Token、人员标识、卡号、车牌、截图/视频 URL 会被脱敏；
- 本阶段仅新增 Domain 与测试，未接入数据库、API、Tool、Runner、真实模型或真实设备。

Phase 5B 交付范围（confirmed 诊断提炼知识候选）：

- `application/knowledge_candidates.py`：隔离 Diagnosis 与 Knowledge 的应用层转换边界；
- 只有状态为 `confirmed` 且存在人工 confirm 记录、候选结论和有效 Evidence 引用的诊断可以生成知识候选；
- 标题、摘要、根因、现象、排查步骤和排除项由结论与四类确定性诊断规则共同提炼；
- 仅保留原诊断、结论和 Evidence ID 以供追溯，不复制完整 Evidence payload；
- 生成结果始终为 `candidate`，不持久化正式知识，也不绕过人工知识审核；
- 已用 Phase 1～4 的真实本地应用链路验证四类 confirmed 诊断均可沉淀候选知识。

Phase 5C 交付范围（知识检索与 RAG）：

- `KnowledgeRepository` 保存知识全生命周期，但诊断检索只返回人工确认知识；
- candidate、rejected、retired knowledge 不会进入 Agent 上下文；
- `EmbeddingPort` 隔离业务代码与具体向量模型，提供 Fake 与 HTTP BGE Adapter；
- 独立 `local-bge-service` 通过 `/v1/embeddings` 共享 `bge-small-zh-v1.5`；
- `HybridKnowledgeRetriever` 使用中文词法检索、BGE 语义检索与加权 RRF 融合；
- BGE 服务异常时确定性降级到关键词检索；
- `knowledge__search` 名称、输入字段和 Evidence 类型保持兼容，动态知识仍落为 `knowledge_sop`；
- 12 条固定检索评测中，Keyword / Vector / Hybrid 的 Recall@1 分别为
  `0.75 / 0.75 / 0.8333`，Recall@3 为 `0.75 / 1.0 / 1.0`，
  MRR 为 `0.75 / 0.8611 / 0.9028`；BGE 容器冷启动约 `7.24s`，
  热查询平均约 `41～52ms/条`。
- `scripts/demo_phase5_knowledge_loop.py` 串联诊断确认、知识候选生成、知识人工确认、
  Repository、Hybrid Retriever 与 `knowledge_sop` Evidence，形成可运行完整闭环。

Phase 6A 交付范围（SQLite 持久化基座）：

- `ports/diagnosis_repository.py`：诊断仓储 Port（`save` / `get` / `update` / `list` / `exists`），
  应用服务改为依赖 Port 而非具体内存实现；
- `domain/redaction.py`：**唯一**脱敏入口（凭证键名、内联凭证、Bearer、URL 凭证、
  安防敏感标识键名，含带边界匹配避免 `pin` 误伤 `spindle`）；
  Domain 构造期即脱敏，Evidence 每次校验都基于最终脱敏内容重算 `content_hash`；
- `adapters/persistence/database.py`：SQLite Engine / Session 工厂与 URL 解析
  （显式参数 > `SECURITY_DIAGNOSIS_DB_URL` > 默认 `sqlite:///./data/security-diagnosis.db`）；
- `adapters/persistence/models.py`：ORM 模型（`diagnosis_cases` / `knowledge_candidates`），
  复杂子结构以 JSON 列保存，仅存在于 adapters 层；
- `adapters/persistence/mapping.py`：Domain ↔ ORM 双向转换，读回时重新构造真正的 Domain 对象；
  写入前对整个聚合做**深层安全规范化**（重新 `model_validate`，让嵌套 validators 再次执行，
  覆盖构造后修改绕过 validator 的场景），且不修改调用方原对象；
- `adapters/persistence/diagnosis_repository.py`、`knowledge_repository.py`：SQLite 仓储实现，
  `IntegrityError` 回滚后按「目标 ID 是否存在」区分 `*AlreadyExistsError` 与
  `RepositoryPersistenceError`，其它 `SQLAlchemyError` 亦映射为 `RepositoryPersistenceError`，
  统一显式 `rollback()`，不泄漏 ORM 异常；
- `application/errors.py`：统一仓储异常（`*NotFoundError` / `*AlreadyExistsError` /
  `RepositoryPersistenceError`），内存与 SQLite 实现对称，保留旧导入路径兼容；
- `migrations/` + `alembic.ini`：Alembic `0001` 迁移，支持 `upgrade head` / `downgrade base` / 再 `upgrade head`；
- 内存仓储保留且与 SQLite 仓储行为一致；`KnowledgeRepository` 检索契约不变。

Phase 6B-1 交付范围（正式运行装配与真实重启恢复）：

- `config.py`：`RuntimeSettings` 运行配置（`repository_mode` / `database_url` /
  `database_echo` / `auto_migrate`），优先级为「显式参数 > 环境变量 > 安全默认」，
  只接受本地 SQLite URL，非法值抛 `RuntimeConfigurationError`，
  且 `repr` / 异常信息不泄漏完整 URL 与凭证；
- `runtime.py`：`upgrade_database(url)` 程序化执行 Alembic `upgrade head`
  （不使用 `create_all`，不依赖调用者 cwd）；`RuntimeContainer` 独占 Engine 生命周期，
  `close()` 可重复调用并 dispose Engine，支持 context manager；
- `scripts/run_api.py`：**正式本地 SQLite 运行入口**（读配置 → 迁移 → 启动 uvicorn →
  退出时 `close()`），默认监听 `127.0.0.1:8000`；
- `scripts/demo_phase6_persistence_restart.py`：真实文件型 SQLite 重启恢复 Demo；
- `scripts/probe_import_side_effects.py`：子进程 import 副作用探针；
- `GET /health` 新增 `repository_mode` / `database_ready`（带兼容默认值，不泄漏路径与 URL）；
- API 异常映射修正：NotFound → 404、AlreadyExists → 409、
  `RepositoryPersistenceError` → 503 `repository_unavailable`（安全文案）。

### 运行入口区分（重要）

| 入口 | 用途 | 仓储 | 副作用 |
|---|---|---|---|
| `create_app()` / 模块级 `api.app.app` | 应用工厂与既有测试 | **内存装配** | 无 |
| `build_container()` / `build_phase1~4_container()` | 评测与 demo | 内存 + FakeLLM | 无 |
| `scripts/run_api.py` | **正式本地运行** | SQLite | 建目录 / 迁移 / 启服务 |

`create_app()` 与 `build_container()` **不是** SQLite 入口：它们保持安全的内存/测试装配，
导入不建目录、不建数据库、不跑迁移。只有 `scripts/run_api.py` 是正式 SQLite 入口。

### 正式 Runtime 的能力边界（重要）

Phase 6B-1 尚未实现四故障域统一 Strategy Router，因此**正式 Runtime 只装配摄像头黑屏诊断**：

- 支持：`camera_black_screen`；
- 不支持：`recording_missing` / `access_card_failed` / `alarm_false_positive`
  （create 与 run 两处都会被拒绝，抛 `UnsupportedFaultTypeError`，API 返回 422
  `unsupported_fault_type`，且不写入任何 Evidence / Conclusion / Review）；
- Phase 1～4 的**独立评测 Container**（`build_phase1~4_container()`）仍然分别支持
  各自故障域，不受该限制影响；
- 四故障域的**正式运行路由**留给后续单独阶段实现；
  不会通过扩大工具 allowlist 来假装支持。

### Domain 不变量（跨故障域护栏）

无论使用哪个 Runtime / LLM / 工具，都必须满足：

```text
conclusion.diagnosis_id == case.diagnosis_id
conclusion.fault_type   == case.fault_type
```

由 `SecurityDiagnosisCase.set_conclusion()` 强制，`CitationPolicy.validate()`
另做一遍防御性校验；违反时抛 `ConclusionFaultTypeMismatch`，且不修改 Case 状态。

### health 语义

`database_ready` 的含义是「**Runtime 数据库组件已成功初始化且 Container 未关闭**」，
不代表数据库实时可连接、文件仍存在或 SQL 必然成功；真正的数据库探活留给后续阶段。

### 乐观锁（Phase 6B-2）

诊断（DiagnosisCase）与知识候选（KnowledgeCandidate）均带 `version` 字段，
由 Repository 用 CAS（compare-and-swap）保护，避免丢失更新：

```text
读取 version=N
  → 修改副本
  → UPDATE ... WHERE id = ? AND version = N       （rowcount==1 → version=N+1）
  → rowcount==0：
       ID 不存在 → NotFound
       ID 存在   → ConcurrentUpdateError（陈旧副本不得覆盖较新状态）
```

要点：

- 新建聚合 `version=0`；首次 `save()` 后为 `1`；每次 `update()` 成功后 `+1`；
- `version` 只代表**成功持久化次数**，普通 Domain 状态变化不会自增；
- 内存 Adapter 使用 `RLock` 实现同一语义，SQLite 使用 CAS UPDATE；
- 冲突时 `ConcurrentUpdateError`，API 映射为 **409** `concurrent_update`，
  消息为「诊断已被其他请求更新，请刷新后重试」，不暴露版本号；
- **不自动重试** Agent / Tool / HumanReview，冲突必须由调用方重新读取后再操作；
- API 创建请求不接受 `version` 输入；
- 本阶段是**乐观锁冲突检测**，不是悲观锁、长事务或分布式锁，
  也不锁住 LLM / Tool 执行过程。

真实并发探针：

```bash
uv run python scripts/demo_phase6_optimistic_lock.py
```

```text
STALE_UPDATE_REJECTED: True
WINNER_VERSION: 2
WINNER_DATA_PRESERVED: True
LOSER_DATA_ABSENT: True
AUTOMATIC_AGENT_RETRY: False
AUTOMATIC_REVIEW_RETRY: False
```

迁移：`migrations/versions/0002_add_aggregate_versions.py` 为两张表各加
`version INTEGER NOT NULL DEFAULT 1`，旧数据升级后为 `1`；
`upgrade head` / `downgrade base` / 再 `upgrade head` 均可逆。

```bash
# 正式本地运行（默认 SQLite + 自动迁移）
uv run python scripts/run_api.py

# 真实重启恢复 Demo
uv run python scripts/demo_phase6_persistence_restart.py
```

可用环境变量：

```text
SECURITY_DIAGNOSIS_REPOSITORY=sqlite
SECURITY_DIAGNOSIS_DB_URL=sqlite:///./data/security-diagnosis.db
SECURITY_DIAGNOSIS_DB_ECHO=false
SECURITY_DIAGNOSIS_AUTO_MIGRATE=true
```

## 快速开始

```bash
uv sync
uv run ruff check .
uv run pytest
uv run python scripts/run_api.py
```

`uv run python scripts/run_api.py` 是**正式本地 SQLite 运行入口**，启动后访问
<http://127.0.0.1:8000/health>，返回统一信封：

```json
{"code": "ok", "message": "ok", "data": {"status": "ok", "service": "security-diagnosis-harness", "version": "0.1.0", "phase": "6B", "repository_mode": "sqlite", "database_ready": true}}
```

应用工厂 `create_app()` 与模块级 `api.app.app` 保持**内存装配**，
导入它们不会建目录、建数据库或执行迁移。

## 目录结构

```text
src/security_diagnosis_harness/
  api/            FastAPI 应用、路由与统一响应
  domain/         领域模型与 Citation Policy（不依赖 FastAPI / SQLAlchemy / LLM SDK）
  ports/          LLMClient / DeviceGateway 抽象契约
  adapters/       FakeLLM、StaticDeviceGateway
  tools/          工具契约、Tool Registry、只读设备工具与知识检索
  agent/          最小受控 ToolLoopRunner
  application/    内存仓储、应用服务、Markdown 报告
  bootstrap/      Container 装配
tests/            领域 / 工具 / 适配器 / Agent / 应用 / API / demo 测试
scripts/          本地启动脚本与摄像头黑屏 demo
samples/          StaticDeviceGateway 使用的本地样例设备数据
```

## 最小闭环

```text
POST /api/v1/diagnoses                 创建诊断（created）
POST /api/v1/diagnoses/{id}/runs       运行 Harness，落成 Evidence 与候选结论
                                       -> Citation Policy 校验
                                       -> waiting_for_confirmation
GET  /api/v1/diagnoses/{id}/evidence   查看 Evidence
POST /api/v1/diagnoses/{id}/review     confirm / reject / request_more_info
GET  /api/v1/diagnoses/{id}/report.md  Markdown 报告
```

一键 demo：

```bash
uv run python scripts/demo_phase0_camera_black_screen.py
```

输出 JSON 包含 `diagnosis_id`、`status`、`evidence_count`、`conclusion_confidence`、
`cited_evidence_ids`、`human_action`、`external_model_called: false` 和报告路径，
报告写入 `demo-output/phase0-camera-black-screen-report.md`（已被 .gitignore 忽略）。

## Harness 约束边界

- 所有工具必须经 `ToolRegistry` 注册和执行，绕过 Registry 直接调用会被拒绝；
- Phase 0 只允许 `READ_ONLY` 工具，注册写操作工具直接失败；
- 工具必须声明权限与风险，权限不足、故障类型不支持、参数非法都转成受控失败结果；
- 工具失败不携带任何 EvidenceDraft，失败不能被包装成证据；
- Runner 有 `max_rounds` / `max_tool_calls` 预算，超预算返回受控失败；
- Runner 不修改 `SecurityDiagnosisCase` 状态，也不把 EvidenceDraft 落成 `DiagnosisEvidence`；
- Citation Policy：`probable` 必须引用至少两类设备事实 Evidence，只引用 SOP 时最多 `possible`；
- 自动测试只使用 FakeLLM 与本地静态样例，不调用真实模型、不访问真实设备。

## 应用服务与 API 边界

- API 层不直接写领域状态，全部委托 `SecurityDiagnosisApplicationService`；
- 应用服务是唯一负责把 `ToolEvidenceDraft` 落成 `DiagnosisEvidence` 的地方；
- 模型结论的 `cited_evidence_ids` 会被最小修正为本次真实落地的 Evidence ID，
  修正后仍然必须过 `CitationPolicy`，不允许绕过；
- 没有设备事实时 `probable` 会被降级为 `possible`，不允许无依据的高可信结论；
- Phase 1 起 `probable` 至少引用两类设备事实 Evidence，不足两类时同样降级为 `possible`；
- Runner 失败时不伪造 Evidence，Case 进入 `waiting_for_input`，没有任何 Evidence 时进入 `inconclusive`；
- `confirmed` 只能由 `POST /review` 的 `confirm` 动作经 `apply_human_review` 产生；
- 异常经受控映射返回 4xx JSON（`code`/`message`），不向调用方抛原始堆栈；
- 报告输出前对 payload 再脱敏一次，凭证字段统一显示为 `***REDACTED***`；
- 不接数据库，使用内存仓储；不读 `.env`，不调用外部网络。

## 领域模型边界

- 领域层只依赖标准库与 pydantic；
- 状态机禁止非法跳转，`confirmed` 不在任何普通状态跳转的合法目标集合中；
- `confirmed` 只能通过 `SecurityDiagnosisCase.apply_human_review(HumanReviewAction.CONFIRM)` 产生；
- `DiagnosisEvidence` 必须属于某个诊断，且带类型、来源、内容 hash、可信度与脱敏标记；
- 设备配置快照中的凭证类字段（password / token / secret / access key 等）在入库前替换为 `***REDACTED***`。

## 文档入口

- [项目定位与总体架构设计](./docs/00-overview/项目定位与总体架构设计.md)
- [旧项目能力复用矩阵](./docs/00-overview/旧项目能力复用矩阵.md)
- [架构图：安防设备诊断 Harness 总览](./docs/01-architecture/security-device-diagnosis-harness-overview.md)
- [Phase 0 实现规格说明](./docs/02-specifications/Phase%200%20实现规格说明.md)
- [Phase 1 摄像头黑屏深化实施规格说明](./docs/02-specifications/Phase%201%20摄像头黑屏深化实施规格说明.md)
- [Phase 2 录像缺失深化实施规格说明](./docs/02-specifications/Phase%202%20录像缺失深化实施规格说明.md)
- [Phase 3 门禁刷卡异常深化实施规格说明](./docs/02-specifications/Phase%203%20门禁刷卡异常深化实施规格说明.md)
- [Phase 4 报警误报深化实施规格说明](./docs/02-specifications/Phase%204%20报警误报深化实施规格说明.md)
- [Phase 5 知识沉淀与 RAG 增强实施规格说明](./docs/02-specifications/Phase%205%20知识沉淀与RAG增强实施规格说明.md)
- [Phase 0 开发总结与 Phase 1 摄像头黑屏深化计划](./docs/03-progress/2026-09-09-Phase0开发总结与Phase1摄像头黑屏深化计划.md)
- [Phase 1 开发总结与 Phase 2 录像缺失深化计划](./docs/03-progress/2026-09-09-Phase1开发总结与Phase2录像缺失深化计划.md)
- [Phase 0 验收标准](./docs/04-validation/Phase%200%20验收标准.md)
- [Phase 1 验收标准](./docs/04-validation/Phase%201%20验收标准.md)
- [Phase 2 验收标准](./docs/04-validation/Phase%202%20验收标准.md)
- [Phase 3 验收标准](./docs/04-validation/Phase%203%20验收标准.md)
- [Phase 4 验收标准](./docs/04-validation/Phase%204%20验收标准.md)
- [Phase 5 验收标准](./docs/04-validation/Phase%205%20验收标准.md)
- [Phase 6A 验收标准](./docs/04-validation/Phase%206A%20验收标准.md)
- [Phase 6B-1 验收标准](./docs/04-validation/Phase%206B-1%20验收标准.md)
- [Phase 6B-2 验收标准](./docs/04-validation/Phase%206B-2%20验收标准.md)

## 最小闭环路线

```text
用户描述设备故障
  -> 识别设备与故障类型
  -> 只读设备工具采集状态、告警、配置
  -> 转换为 Evidence
  -> 召回安防知识/SOP
  -> LLM 在 Harness 约束下推理
  -> Citation Policy 校验结论引用
  -> 人工确认/驳回/继续调查
  -> confirmed 后沉淀知识候选
```

## 安全约束

- 不提交 `.env`、API Key、Token、邮箱授权码、真实设备凭证。
- Phase 0 默认只使用 Fake LLM，不访问外部模型。
- Phase 0 只做只读设备事实采集，不做重启、配置下发、开门、静音等写操作。
