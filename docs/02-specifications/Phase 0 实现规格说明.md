# Phase 0 实现规格说明

> 阶段目标：搭建安防设备诊断 Harness 的最小可运行骨架，跑通“摄像头黑屏”只读诊断闭环。

## 1. Phase 0 的目标

Phase 0 不追求设备能力丰富，而是验证新项目的核心架构是否成立：

```text
设备故障描述
  -> 设备事实采集
  -> Evidence 留存
  -> LLM 受控推理
  -> Citation Policy
  -> 人工确认
  -> 报告与评测
```

## 2. Phase 0A：项目骨架与领域模型

### 2.1 目标

建立 Python 后端基础工程和安防设备诊断领域对象。

### 2.2 建议技术栈

- Python 3.12
- uv
- FastAPI
- Pydantic
- SQLAlchemy Async
- SQLite
- Alembic
- pytest
- Ruff

### 2.3 领域对象

| 对象 | 说明 |
|---|---|
| `SecurityDiagnosisCase` | 一次安防设备诊断 |
| `SecurityFaultType` | 故障类型：camera_black_screen、recording_missing、access_card_failed、alarm_false_positive |
| `SecurityDiagnosisStatus` | created、investigating、waiting_for_input、waiting_for_confirmation、confirmed、rejected、inconclusive |
| `Device` | 设备主数据 |
| `DeviceSnapshot` | 设备状态快照 |
| `DeviceAlarmEvent` | 设备告警事件 |
| `DeviceConfigSnapshot` | 设备配置快照 |
| `DiagnosisEvidence` | 诊断证据 |
| `DiagnosisConclusion` | 模型候选结论 |
| `HumanReview` | 人工确认、驳回或继续调查 |

## 3. Phase 0B：Harness 与只读工具

### 3.1 核心组件

| 组件 | 职责 |
|---|---|
| `LLMClient Port` | 隔离 Fake LLM 与真实模型 |
| `Tool Registry` | 控制工具注册、白名单、参数、权限、风险、预算 |
| `DeviceGateway Port` | 抽象设备状态、告警、配置读取 |
| `StaticDeviceGateway` | 从本地 JSON 读取设备样例 |
| `ToolLoopRunner` | 受控 Agent Loop |
| `CitationPolicy` | 校验结论引用的 Evidence |

### 3.2 Phase 0 只读工具

| 工具名 | 权限 | 风险 | 输入 | 输出 |
|---|---|---|---|---|
| `device__query_status` | `device:read` | READ_ONLY | `device_id` | 设备在线、通道、码流、录像状态 |
| `device__search_alarm_events` | `device:read` | READ_ONLY | `device_id`、`keyword`、`limit` | 告警事件列表 |
| `device__read_config_snapshot` | `device:read` | READ_ONLY | `device_id` | 配置快照 |
| `knowledge__search` | `knowledge:read` | READ_ONLY | `query` | SOP / 故障模式 |

## 4. Phase 0C：API、报告与评测

### 4.1 最小 API

| API | 说明 |
|---|---|
| `POST /api/v1/diagnoses` | 创建诊断 |
| `POST /api/v1/diagnoses/{id}/runs` | 启动一次诊断运行 |
| `GET /api/v1/diagnoses/{id}` | 查询诊断 |
| `GET /api/v1/diagnoses/{id}/evidence` | 查询证据 |
| `POST /api/v1/diagnoses/{id}/review` | 人工确认、驳回或继续调查 |
| `GET /api/v1/diagnoses/{id}/report.md` | 查询 Markdown 报告 |

### 4.2 最小演示案例

案例：摄像头黑屏。

样例事实：

- 设备在线；
- 通道在线；
- 主码流异常；
- 告警事件包含 `STREAM_PUBLISH_FAILED` 或 `ENCODER_TIMEOUT`；
- 配置快照中码率或编码参数异常；
- 知识库中有“摄像头黑屏排查 SOP”。

期望结论：

```text
probable: 摄像头黑屏更可能由码流发布失败或编码器异常导致
evidence: 引用设备状态、告警事件、配置快照
next_steps: 检查编码器、降低码率、确认平台拉流状态
```

## 5. 不做什么

Phase 0 明确不做：

- 真实设备接入；
- 真实视频流播放；
- 重启摄像头；
- 开门、布撤防、配置下发；
- 多 Agent；
- 模型微调；
- 向量数据库；
- 工单系统；
- 企业权限系统。

## 6. 交付物

- 可运行 Python 工程；
- README 和 docs 导航；
- 领域模型单元测试；
- 工具 Registry 单元测试；
- StaticDeviceGateway Adapter 测试；
- 摄像头黑屏 demo；
- Phase 0 验收文档；
- 面试可讲的项目定位说明。

## 7. 面试表达

> Phase 0 我刻意没有接真实设备，也没有做写操作，而是先把 Harness 底座跑通。因为安防设备诊断的关键不是工具越多越好，而是模型必须基于可追踪设备事实推理，并且最终结论要经过引用校验和人工确认。
