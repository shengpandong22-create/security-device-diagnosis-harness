# Phase 2 验收标准

> 验收对象：录像缺失 / 录像异常诊断深化。
> Phase 2 分三段：2A 领域模型、2B 只读工具与样例案例、2C 规则与评测。
> 本文档不接真实设备、不接真实模型、不接数据库。

## 1. Phase 2A：录像诊断领域模型（已完成）

### 1.1 工程验收

- [x] `uv run ruff check .` 通过；
- [x] `uv run pytest` 通过（315 个测试）；
- [x] Phase 0 demo 仍可运行；
- [x] Phase 1 eval 仍 5/5；
- [x] 领域模型只依赖标准库与 pydantic；
- [x] 不新增 API、Tool、Runner、DeviceGateway 改动、样例 JSON、eval 脚本；
- [x] 不新增数据库 / SQLAlchemy / Alembic / 真实模型 SDK；
- [x] 不提交真实设备 IP、账号、密码、Token、邮箱授权码。

### 1.2 事实模型验收

| 需要表达的事实 | 模型 | 落地方式 |
|---|---|---|
| 通道是否配置了录像计划 | `RecordingPlanSnapshot` | `time_ranges`、`has_time_ranges` |
| 录像计划是否启用 | `RecordingPlanSnapshot` | `status`、`is_plan_active` |
| 计划覆盖哪些时间段 | `RecordingTimeRange` | `start` / `end` / `weekdays` |
| 录像模式 | `RecordingPlanSnapshot` | `mode`（continuous / event_triggered / manual） |
| 保留天数 | `RecordingPlanSnapshot` | `retention_days`（>= 0） |
| 存储池是否正常 | `StorageSnapshot` | `status`、`is_available` |
| 容量是否不足 | `StorageSnapshot` | `free_percent`、`is_capacity_low` |
| 指定时间段是否存在录像 | `PlaybackCheckResult` | `file_count`、`has_files`、`is_missing` |
| 录像是否可回放 | `PlaybackCheckResult` | `playable` |
| 回放失败原因 | `PlaybackCheckResult` | `failure_reason`、`status` |

### 1.3 约束验收

- [x] `RecordingPlanSnapshot` 拒绝空 `device_id` / `channel_id` / `plan_id`；
- [x] `retention_days` 存在时必须 >= 0；
- [x] `extra` 中凭证类字段被替换为 `***REDACTED***`；
- [x] `time_ranges` 允许为空；
- [x] `status=enabled` 且 `time_ranges` 为空时，通过 `has_schedule_gap` 表达
      "计划启用但无有效时间段"的异常状态；
- [x] `RecordingTimeRange.weekdays` 取值只能是 1～7；
- [x] `RecordingTimeRange` 支持跨天（`start > end`），通过 `crosses_midnight` 判断；
- [x] `StorageSnapshot` 拒绝负容量（total_gb / free_gb）；
- [x] `StorageSnapshot.used_percent` 范围限制在 0～100；
- [x] `StorageSnapshot.is_capacity_low` 可判断容量不足（阈值 10%）；
- [x] `PlaybackCheckResult` 拒绝 `end_at <= start_at`；
- [x] `PlaybackCheckResult.file_count` 不能为负；
- [x] `PlaybackCheckResult` 的 `status` 与 `playable` 不允许自相矛盾：
      `available` 必须 `playable=true`，`missing` / `corrupted` / `index_missing`
      必须 `playable=false`；
- [x] `PlaybackCheckResult` 的 `status` 与 `file_count` 不允许自相矛盾：
      `available` 必须 `file_count > 0`，`missing` 必须 `file_count == 0`；
      `corrupted` 和 `index_missing` 不强制 `file_count`，用于兼容不同平台的文件可见性；
- [x] 模型不包含 `confidence` / `root_cause` / `conclusion` 等结论字段；
- [x] 模块源码不含 `fastapi` / `sqlalchemy` / `alembic` / `openai` / `httpx` / `requests`。

### 1.4 枚举

| 枚举 | 取值 |
|---|---|
| `RecordingPlanStatus` | `enabled` / `disabled` / `misconfigured` |
| `RecordingMode` | `continuous` / `event_triggered` / `manual` |
| `StorageStatus` | `normal` / `full` / `offline` / `degraded` |
| `PlaybackStatus` | `available` / `missing` / `corrupted` / `index_missing` |

## 2. Phase 2B：只读工具与样例案例（已完成）

### 2.1 工程验收

- [x] `uv run ruff check .` 通过；
- [x] `uv run pytest` 通过（377 个测试）；
- [x] Phase 0 demo 仍可运行；
- [x] Phase 1 eval 仍 5/5；
- [x] 不新增 API、不修改 ToolLoopRunner / CitationPolicy、不新增规则推断 / eval 脚本；
- [x] 不修改 `pyproject.toml`，不新增 HTTP 客户端；
- [x] 不读取 `.env`，不提交真实设备 IP、账号、密码、Token、邮箱授权码。

### 2.2 DeviceGateway Port 扩展

| 方法 | 返回类型 |
|---|---|
| `query_recording_plan(device_id, channel_id)` | `RecordingPlanSnapshot` |
| `query_storage_status(device_id, channel_id)` | `StorageSnapshot` |
| `check_recording_playback(device_id, channel_id, start_at, end_at)` | `PlaybackCheckResult` |

- [x] 三个方法均只读，返回领域模型（不是 dict）；
- [x] 设备不存在抛 `DeviceNotFoundError`；
- [x] 通道/录像事实缺失抛 `DeviceGatewayDataError`；
- [x] `check_recording_playback` 支持按覆盖优先、重叠次之匹配样例窗口；
- [x] naive 与 aware 时间统一按 UTC 处理，不抛 `TypeError`。

### 2.3 只读工具

| 工具 | EvidenceType | observation 必含 |
|---|---|---|
| `recording__query_plan` | `recording_plan` | 计划状态、模式、时间段数 |
| `storage__query_status` | `storage_status` | 存储状态、剩余容量、是否容量不足 |
| `recording__check_playback` | `playback_check` | 回放状态、可回放、文件数、失败原因 |

- [x] 全部 `READ_ONLY`，权限均为 `device:read`；
- [x] 必须经 ToolRegistry（`invoked_by_registry` 闸门不变）；
- [x] 参数非法 / 权限不足 / 网关异常 → 受控失败且不携带 EvidenceDraft；
- [x] `recording__check_playback` 解析 ISO 8601（含 `Z` 后缀、naive、`+08:00`）；
- [x] `recording__check_playback` 拒绝 `end_at <= start_at` 与超过 31 天的时间窗。

### 2.4 新增 EvidenceType

- [x] `recording_plan` / `storage_status` / `playback_check`（snake_case，与既有风格一致）；
- [x] 三者尚未纳入 `DEVICE_FACT_EVIDENCE_TYPES`（留给 Phase 2C 与规则口径一起调整）。

### 2.5 固定样例案例

样例文件：`samples/devices/recording_missing_cases.json`（5 个案例，全部 2026-09-08 ~ 09-09 回放窗口）

| case_id | device_id | 录像计划 | 存储 | 回放 |
|---|---|---|---|---|
| `recording_plan_disabled` | `cam-rec-plan-disabled-01` | disabled / manual | normal | missing |
| `recording_schedule_gap` | `cam-rec-schedule-gap-01` | enabled / event_triggered（仅工作日 09:00-18:00） | normal | missing |
| `storage_full` | `cam-rec-storage-full-01` | enabled / continuous（全天） | full | missing |
| `storage_offline` | `cam-rec-storage-offline-01` | enabled / continuous（全天） | offline | missing |
| `playback_index_missing` | `cam-rec-index-missing-01` | enabled / continuous（全天） | normal | index_missing（file_count=12） |

- [x] 样例无真实 IP / 账号 / 密码 / Token / 邮箱授权码；
- [x] `admin_password` 为占位串且被 `DeviceConfigSnapshot` 脱敏（有测试断言）。

## 3. Phase 2C：规则、报告与评测（未开始）

- [ ] `application/recording_diagnosis_rules.py`
- [ ] `scripts/eval_phase2_recording_missing.py`
- [ ] 报告展示候选根因、证据链、排查顺序、排除项

## 4. Phase 2 Definition of Done

- [x] Phase 2A 领域模型与测试完成；
- [x] Phase 2B 只读工具与样例案例完成；
- [ ] Phase 2C 规则、报告与固定评测完成；
- [ ] label_accuracy >= 0.75；
- [ ] citation_compliance == 1.0；
- [ ] sensitive_leak_count == 0；
- [ ] external_model_called == false；
- [ ] Git 工作区干净并推送。
