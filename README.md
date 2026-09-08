# Security Device Diagnosis Harness

> 面向安防设备运维场景的可信诊断 Agent Harness。

本项目不是把服务日志丢给大模型生成答案，而是把 LLM 放入一个受控诊断 Harness：由确定性工程层负责设备事实采集、工具权限、Evidence 留存、引用校验、人工确认和评测回归，LLM 只在这些边界内完成推理。

## 项目定位

- 业务域：安防设备运维诊断，优先覆盖摄像头黑屏、录像缺失、门禁刷卡异常、报警误报等场景。
- 技术目标：验证 Agent 如何在设备状态、告警事件、配置快照、知识库 SOP 和人工反馈之间形成可信闭环。
- 当前阶段：Phase 0A、Phase 0B、Phase 0C 已完成。
- 重要边界：本项目不继承应用日志诊断主线，不迁移 Java Lab、NPE、服务日志、源码诊断、Gateway/Nacos/Trace 作为主叙事。

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0A | 项目骨架与领域模型 | 已完成 |
| Phase 0B | Harness 与只读工具 | 已完成 |
| Phase 0C | API、报告与 demo | 已完成 |

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

## 快速开始

```bash
uv sync
uv run ruff check .
uv run pytest
uv run python scripts/run_api.py
```

启动后访问 <http://127.0.0.1:8000/health>，返回统一信封：

```json
{"code": "ok", "message": "ok", "data": {"status": "ok", "service": "security-diagnosis-harness", "version": "0.1.0", "phase": "0A"}}
```

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
- Citation Policy：`probable` 必须引用设备事实 Evidence，只引用 SOP 时最多 `possible`；
- 自动测试只使用 FakeLLM 与本地静态样例，不调用真实模型、不访问真实设备。

## 应用服务与 API 边界

- API 层不直接写领域状态，全部委托 `SecurityDiagnosisApplicationService`；
- 应用服务是唯一负责把 `ToolEvidenceDraft` 落成 `DiagnosisEvidence` 的地方；
- 模型结论的 `cited_evidence_ids` 会被最小修正为本次真实落地的 Evidence ID，
  修正后仍然必须过 `CitationPolicy`，不允许绕过；
- 没有设备事实时 `probable` 会被降级为 `possible`，不允许无依据的高可信结论；
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
- [Phase 0 验收标准](./docs/04-validation/Phase%200%20验收标准.md)

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
