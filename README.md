# Security Device Diagnosis Harness

> 面向安防设备运维场景的可信诊断 Agent Harness。

本项目不是把服务日志丢给大模型生成答案，而是把 LLM 放入一个受控诊断 Harness：由确定性工程层负责设备事实采集、工具权限、Evidence 留存、引用校验、人工确认和评测回归，LLM 只在这些边界内完成推理。

## 项目定位

- 业务域：安防设备运维诊断，优先覆盖摄像头黑屏、录像缺失、门禁刷卡异常、报警误报等场景。
- 技术目标：验证 Agent 如何在设备状态、告警事件、配置快照、知识库 SOP 和人工反馈之间形成可信闭环。
- 当前阶段：Phase 0A 已完成（项目骨架与领域模型），Phase 0B/0C 未开始。
- 重要边界：本项目不继承应用日志诊断主线，不迁移 Java Lab、NPE、服务日志、源码诊断、Gateway/Nacos/Trace 作为主叙事。

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0A | 项目骨架与领域模型 | 已完成 |
| Phase 0B | Harness 与只读工具 | 未开始 |
| Phase 0C | API、报告与评测 | 未开始 |

Phase 0A 交付范围：

- Python 3.12 + uv + pytest + ruff 工程骨架；
- 最小 FastAPI 应用与 `GET /health`；
- 安防设备诊断领域模型与状态机；
- 领域模型测试与最小 API 测试。

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
  api/            FastAPI 应用与统一响应
  domain/         领域模型（不依赖 FastAPI / SQLAlchemy / LLM SDK）
tests/            领域测试与 API 测试
scripts/          本地启动脚本
samples/          后续阶段使用的本地样例设备数据
```

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
