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

## 2. Phase 2B：只读工具与样例案例（未开始）

- [ ] `recording__query_plan`
- [ ] `storage__query_status`
- [ ] `recording__check_playback`
- [ ] 固定案例：`recording_plan_disabled`、`recording_schedule_gap`、`storage_full`、
      `storage_offline`、`playback_index_missing`

## 3. Phase 2C：规则、报告与评测（未开始）

- [ ] `application/recording_diagnosis_rules.py`
- [ ] `scripts/eval_phase2_recording_missing.py`
- [ ] 报告展示候选根因、证据链、排查顺序、排除项

## 4. Phase 2 Definition of Done

- [ ] Phase 2A 领域模型与测试完成（已完成）；
- [ ] Phase 2B 只读工具与样例案例完成；
- [ ] Phase 2C 规则、报告与固定评测完成；
- [ ] label_accuracy >= 0.75；
- [ ] citation_compliance == 1.0；
- [ ] sensitive_leak_count == 0；
- [ ] external_model_called == false；
- [ ] Git 工作区干净并推送。
