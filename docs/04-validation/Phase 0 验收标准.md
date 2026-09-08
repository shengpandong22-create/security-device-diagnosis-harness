# Phase 0 验收标准

> 本文档定义 Phase 0 完成标准。后续实现代码时，必须以这里为验收口径。

## 1. 工程验收

- [x] 项目可通过 `uv sync` 安装依赖；
- [x] `uv run pytest` 全绿；
- [x] `uv run ruff check .` 无错误；
- [x] `.env`、API Key、Token、设备凭证不会进入 Git；
- [x] README 能说明项目定位、运行方式和安全边界。

## 2. 领域验收

- [x] `SecurityDiagnosisCase` 能表达一次设备诊断；
- [x] 状态机禁止非法跳转；
- [x] `confirmed` 只能由人工确认动作产生；
- [x] `SecurityFaultType` 至少包含 `camera_black_screen`；
- [x] 领域层不依赖 FastAPI、SQLAlchemy、HTTP 客户端或具体 LLM SDK。

## 3. 工具验收

- [x] 所有工具必须通过 Tool Registry 注册；
- [x] 未注册工具必须拒绝；
- [x] 无权限工具必须拒绝；
- [x] 非法参数必须拒绝；
- [x] 工具必须声明风险等级；
- [x] Phase 0 所有设备工具均为 READ_ONLY；
- [x] 工具失败不能伪造成 Evidence。

## 4. Evidence 验收

- [x] 设备状态、告警事件、配置快照可以转换为 Evidence；
- [x] Evidence 必须属于某个 Diagnosis；
- [x] Evidence 有类型、来源、hash、可信度、脱敏状态；
- [x] 相同诊断下同内容按 hash 去重；
- [x] 结论引用的 Evidence ID 必须属于当前诊断；
- [x] 只引用知识库 SOP 时，结论最多为 possible；
- [x] probable 至少引用一个设备事实 Evidence。

## 5. 安全验收

- [ ] 原始密码、Token、secret、连接串不能原样入库；
- [ ] LLM 不能看到未脱敏敏感内容；
- [ ] LLM 不能直接执行设备写操作；
- [ ] Phase 0 不访问真实设备网络；
- [ ] 自动测试不调用真实模型。

## 6. Demo 验收

- [x] 摄像头黑屏 demo 可一键运行；
- [x] demo 使用 Fake LLM 或固定模型响应；
- [x] demo 输出 diagnosis_id；
- [x] demo 输出 Evidence 数量；
- [x] demo 输出候选结论；
- [x] demo 输出 Markdown 报告路径；
- [x] demo 不依赖真实设备、不依赖真实外部模型。

## 7. 面试验收

学完 Phase 0 后，应能回答：

1. 为什么这个项目不叫普通 ChatBot？
2. Harness 约束了 LLM 哪些自由？
3. 设备状态、告警、配置分别能证明什么？
4. 为什么告警存在不能直接等于根因？
5. 为什么 confirmed 必须人工确认？
6. 旧应用诊断项目和新安防项目是什么关系？
7. 企业落地还需要补哪些系统？

## 8. Phase 0C 完成状态

Phase 0C（API 闭环、EvidenceDraft 落地、人工 Review、Markdown 报告、摄像头黑屏 demo）已完成，
覆盖本文档第 6 节 Demo 验收项：

| 组件 | 落点 | 说明 |
|---|---|---|
| 内存仓储 | `application/repository.py` | 只用内存 dict，`save` / `get` / `list` / `update`，未找到抛 `DiagnosisNotFoundError` |
| 应用服务 | `application/diagnoses.py` | `create_diagnosis` / `run_diagnosis` / `get_diagnosis` / `list_evidence` / `review_diagnosis` / `render_report` |
| 报告渲染 | `application/reports.py` | Markdown 报告，输出前对 payload 再脱敏 |
| 装配 | `bootstrap/container.py` | StaticDeviceGateway + FakeLLM + 四个只读工具 + Runner + 应用服务 |
| 诊断 API | `api/routes/diagnoses.py` | `POST /diagnoses`、`GET /{id}`、`POST /{id}/runs`、`GET /{id}/evidence`、`POST /{id}/review`、`GET /{id}/report.md` |
| Demo | `scripts/demo_phase0_camera_black_screen.py` | create -> run -> evidence -> confirm -> report，输出 JSON 摘要 |

### 应用服务闭环口径

| 环节 | 行为 |
|---|---|
| EvidenceDraft 落地 | 只有成功工具结果的草稿会落成 `DiagnosisEvidence`，失败不产生 Evidence |
| 空引用修正 | `probable` 引用第一条设备事实 Evidence；`possible` 引用第一条 Evidence |
| 无设备事实的 probable | 降级为 `possible`（不允许无依据的高可信结论） |
| 没有任何 Evidence | Case 进入 `inconclusive`，不生成结论 |
| Runner 失败 | Case 进入 `waiting_for_input`，不伪造 Evidence |
| CitationPolicy | 修正后的结论仍必须过校验，拒绝则进入 `inconclusive` 且不落结论 |
| 状态推进 | 应用服务是唯一推进 Case 状态的地方，成功运行后进入 `waiting_for_confirmation` |
| confirmed | 只能由 `POST /{id}/review` 的 `confirm` 经 `apply_human_review` 产生 |
| 错误响应 | 领域/应用异常映射为 404 / 409 / 422 的受控 JSON，不抛原始堆栈 |

Phase 0C 仍然不接数据库、不接 Alembic、不接 SQLAlchemy、不调用真实模型、不访问真实设备。

## 9. Phase 0B 完成状态

Phase 0B（Harness 与只读工具基础）已完成，覆盖本文档第 3、4 节验收项：

| 组件 | 落点 | 说明 |
|---|---|---|
| LLMClient Port | `ports/llm.py` | `ChatMessage` / `ToolCall` / `LLMRequest` / `LLMResponse` / `LLMClient`，不含任何 API Key |
| FakeLLM | `adapters/llm/fake.py` | 回放预设响应并记录请求，不访问网络 |
| DeviceGateway Port | `ports/device_gateway.py` | `query_status` / `search_alarm_events` / `read_config_snapshot` |
| StaticDeviceGateway | `adapters/device_gateway/static.py` | 只读本地 JSON，文件缺失/JSON 错误/设备不存在均抛明确异常 |
| Tool Contract | `tools/contracts.py` | `ToolRiskLevel` / `ToolPermission` / `ToolExecutionContext` / `ToolExecutionResult` / `ToolEvidenceDraft` |
| Tool Registry | `tools/registry.py` | 重复注册、未知工具、无权限、故障类型不支持、参数非法、工具异常全部转成受控失败 |
| 只读工具 | `tools/device_status.py`、`device_alarm_events.py`、`device_config.py`、`knowledge_search.py` | `device__query_status`、`device__search_alarm_events`、`device__read_config_snapshot`、`knowledge__search` |
| ToolLoopRunner | `agent/runner.py` | `max_rounds` / `max_tool_calls` 预算，不修改 Case 状态，不落 Evidence |
| CitationPolicy | `domain/citation_policy.py` | 任何结论必须至少引用 1 条 Evidence；probable 必须引用设备事实；只引用 SOP 最多 possible |

### Citation Policy 校验口径

| 规则 | 结论 |
|---|---|
| `cited_evidence_ids` 为空 | 拒绝（`possible` 与 `probable` 一视同仁，不允许零引用结论） |
| 只引用 `knowledge_sop` + `possible` | 通过 |
| 只引用 `knowledge_sop` + `probable` | 拒绝 |
| 引用 `device_status` / `device_alarm` / `device_config` + `probable` | 通过 |
| 引用了不属于当前诊断的 Evidence ID | 拒绝 |
| 结论 `diagnosis_id` 与 Case 不一致 | 拒绝 |
| 结论可信度为 `confirmed` | 拒绝（模型不能产生 confirmed） |

Phase 0B 未覆盖（属于 Phase 0C）：诊断/review/report API、demo 脚本、Evidence 持久化、评测回归。
Runner 只返回模型草稿，不执行 CitationPolicy，校验在 Phase 0C 应用服务落地时执行。

## 10. Phase 0A 完成状态

Phase 0A（项目骨架与领域模型）已完成，覆盖本文档第 1、2 节全部验收项：

| 验收项 | 结果 |
|---|---|
| `uv sync` / `uv run pytest` / `uv run ruff check .` | 通过（30 个测试） |
| `GET /health` | 返回 200，统一信封 `{"code": "ok", "data": {...}}` |
| 领域模型 | `SecurityDiagnosisCase`、`SecurityFaultType`、`SecurityDiagnosisStatus`、`Device`、`DeviceSnapshot`、`DeviceAlarmEvent`、`DeviceConfigSnapshot`、`DiagnosisEvidence`、`DiagnosisConclusion`、`HumanReview` |
| 状态机 | `ALLOWED_STATUS_TRANSITIONS` 中没有任何状态可以跳到 `confirmed` |
| confirmed 来源 | 仅 `apply_human_review(HumanReviewAction.CONFIRM)`，且要求状态为 `waiting_for_confirmation` 且已有候选结论 |
| Evidence | `diagnosis_id` 必填，跨诊断挂接抛 `EvidenceDiagnosisMismatch`，同内容按 hash 去重 |

Phase 0A 未覆盖（属于 Phase 0B/0C）：工具验收、Evidence 转换与 Citation Policy、Demo 验收。

## 11. Definition of Done

Phase 0 完成时，至少满足：

- [x] 有一条摄像头黑屏端到端诊断链路；
- [x] 有至少 15 个单元/集成测试（当前 155 个）；
- [ ] 有一份验收记录；
- [ ] 有一份面试讲解材料；
- [x] Git 工作区干净；
- [ ] 已推送到 GitHub。
