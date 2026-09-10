# Phase 3 验收标准

> 验收对象：门禁刷卡异常诊断深化。  
> Phase 3 分三段：3A 领域模型、3B 只读工具与样例案例、3C 规则与评测。  
> 本文档不接真实设备、不接真实模型、不接数据库。

## 1. Phase 3A：门禁领域模型（已完成）

### 1.1 工程验收

- [x] `uv run ruff check .` 通过；
- [x] `uv run pytest` 通过；
- [x] Phase 0 demo 仍可运行；
- [x] Phase 1 eval 仍 5/5；
- [x] Phase 2 eval 仍 5/5；
- [x] 领域模型只依赖标准库与 pydantic；
- [x] 不新增 API、Tool、Runner、DeviceGateway 改动、样例 JSON、eval 脚本；
- [x] 不新增数据库 / SQLAlchemy / Alembic / 真实模型 SDK；
- [x] 不提交真实设备 IP、账号、密码、Token、邮箱授权码、生物特征数据。

### 1.2 事实模型验收

| 需要表达的事实 | 模型 | 落地方式 |
|---|---|---|
| 控制器是否在线 | `AccessControllerSnapshot` | 已落地：`status`、`last_seen_at` |
| 控制器是否健康 | `AccessControllerSnapshot` | 已落地：`health`、`last_error` |
| 门当前状态 | `DoorSnapshot` | 已落地：`door_status` |
| 门锁状态 | `DoorSnapshot` | 已落地：`lock_status`、`has_lock_error` |
| 凭证类型 | `CredentialSnapshot` | 已落地：`credential_type` |
| 凭证是否有效 | `CredentialSnapshot` | 已落地：`status`、`expires_at`、`is_valid` |
| 是否有门权限 | `AccessPolicySnapshot` | 已落地：`allowed`、`door_id`、`person_id` |
| 授权时段 | `AccessPolicySnapshot` | 已落地：`time_ranges`、`valid_from`、`valid_until` |
| 刷卡事件与拒绝原因 | `AccessEvent` | 已落地：`decision`、`deny_reason`、`occurred_at` |

### 1.3 安全与一致性验收

- [x] 卡号、人脸特征、指纹、PIN、Token、secret 不得原样保存；
- [x] `extra` 中凭证类字段统一脱敏为 `***REDACTED***`；
- [x] 空 device_id / door_id / credential_id / person_id 应被拒绝；
- [x] 时间窗口可以表达跨天授权；
- [x] 模型不包含 `confidence` / `root_cause` / `conclusion` 等结论字段；
- [x] 模块源码不含 `fastapi` / `sqlalchemy` / `alembic` / `openai` / `httpx` / `requests`。

### 1.4 Phase 3A 完成状态

Phase 3A 只完成门禁领域事实建模，不进入工具、样例、规则和评测。

| 项目 | 当前结论 |
|---|---|
| 新增领域文件 | `src/security_diagnosis_harness/domain/access.py` |
| 新增测试文件 | `tests/domain/test_access.py` |
| 领域测试 | 覆盖控制器、门状态、凭证、授权策略、刷卡事件、脱敏和依赖边界 |
| confirmed 边界 | 未新增任何可产生 confirmed 的入口 |
| 后续衔接 | Phase 3B 可在这些模型之上新增 DeviceGateway 只读方法、Static Adapter 样例和门禁工具 |

## 2. Phase 3B：只读工具与样例案例（未开始）

- [ ] 新增门禁类 DeviceGateway 只读方法；
- [ ] StaticDeviceGateway 支持门禁样例数据；
- [ ] 新增 READ_ONLY 门禁工具；
- [ ] 所有门禁工具要求 `device:read`；
- [ ] 工具失败不产生 EvidenceDraft；
- [ ] 固定案例至少覆盖 5 类典型刷卡异常。

建议固定案例：

| case_id | 期望候选根因 |
|---|---|
| `credential_frozen` | `credential_invalid_or_frozen` |
| `permission_denied` | `permission_not_granted` |
| `time_window_denied` | `access_time_window_denied` |
| `controller_offline` | `controller_offline_or_no_response` |
| `door_lock_jammed` | `door_lock_or_sensor_issue` |

## 3. Phase 3C：规则、报告与评测（未开始）

- [ ] 门禁类 EvidenceType 纳入 CitationPolicy 设备事实集合；
- [ ] 新增 `application/access_diagnosis_rules.py`；
- [ ] 报告展示门禁候选根因、证据链、排查顺序、排除项；
- [ ] 新增 `scripts/eval_phase3_access_card_failed.py`；
- [ ] Phase 3 eval 输出 JSON 和 Markdown；
- [ ] Phase 0/1/2 回归不退化。

## 4. Phase 3 Definition of Done

- [x] Phase 3A 领域模型与测试完成；
- [ ] Phase 3B 只读工具与样例案例完成；
- [ ] Phase 3C 规则、报告与固定评测完成；
- [ ] `label_accuracy == 1.0`；
- [ ] `citation_compliance == 1.0`；
- [ ] `sensitive_leak_count == 0`；
- [ ] `external_model_called == false`；
- [ ] Git 工作区干净并推送。
