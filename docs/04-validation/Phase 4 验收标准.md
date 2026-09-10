# Phase 4 验收标准

> 验收对象：报警误报 / 误触发诊断深化。  
> Phase 4 分三段：4A 领域模型、4B 只读工具与样例案例、4C 规则与评测。  
> 本文档不接真实设备、不接真实模型、不接数据库。

## 1. Phase 4A：报警误报领域模型（已完成）

### 1.1 工程验收

- [x] `uv run ruff check .` 通过；
- [x] `uv run pytest` 通过；
- [x] Phase 0 demo 仍可运行；
- [x] Phase 1 eval 仍 5/5；
- [x] Phase 2 eval 仍 5/5；
- [x] Phase 3 eval 仍 5/5；
- [x] 领域模型只依赖标准库与 pydantic；
- [x] 不新增 API、Tool、Runner、DeviceGateway 改动、样例 JSON、eval 脚本；
- [x] 不新增数据库 / SQLAlchemy / Alembic / 真实模型 SDK；
- [x] 不提交真实设备 IP、账号、密码、Token、邮箱授权码、真实视频截图。

### 1.2 事实模型验收

| 需要表达的事实 | 模型 | 验收要求 |
|---|---|---|
| 报警规则配置 | `AlarmRuleSnapshot` | 已落地：启用状态、报警类型、灵敏度、阈值、防抖时间、布防时段 |
| 触发信号 | `AlarmSignalSnapshot` | 已落地：信号值、阈值、噪声等级、信号状态 |
| 环境干扰 | `AlarmEnvironmentSnapshot` | 已落地：雨、雾、强光、风、夜间、阴影等干扰 |
| 复核结果 | `AlarmVerificationSnapshot` | 已落地：视频/人工复核是否发现真实目标 |
| 关联告警 | `AlarmCorrelationSnapshot` | 已落地：短时间重复告警、相邻设备关联告警、孤立事件 |

### 1.3 安全与一致性验收

- [x] 摄像头画面 URL、人员信息、真实设备地址、Token、secret 不得原样保存；
- [x] `extra` 中凭证类字段统一脱敏为 `***REDACTED***`；
- [x] 空 device_id / alarm_id / rule_id 应被拒绝；
- [x] 布防时间段可以表达跨天；
- [x] 模型不包含 `confidence` / `root_cause` / `conclusion` 等结论字段；
- [x] 模块源码不含 `fastapi` / `sqlalchemy` / `alembic` / `openai` / `httpx` / `requests`。

### 1.4 Phase 4A 完成状态

| 项目 | 当前结论 |
|---|---|
| 新增领域文件 | `src/security_diagnosis_harness/domain/alarm.py` |
| 新增测试文件 | `tests/domain/test_alarm.py` |
| 领域测试 | 覆盖报警规则、触发信号、环境干扰、复核结果、关联告警、脱敏和依赖边界 |
| confirmed 边界 | 未新增任何可产生 confirmed 的入口 |
| 后续衔接 | Phase 4B 可在这些模型之上新增 DeviceGateway 只读方法、Static Adapter 样例和报警工具 |

## 2. Phase 4B：只读工具与样例案例（已完成）

- [x] 新增报警类 DeviceGateway 只读方法；
- [x] StaticDeviceGateway 支持报警误报样例数据；
- [x] 新增 READ_ONLY 报警工具；
- [x] 所有报警工具要求 `device:read`；
- [x] 工具失败不产生 EvidenceDraft；
- [x] 固定案例至少覆盖 5 类典型报警误报。

建议固定案例：

| case_id | 期望候选根因 |
|---|---|
| `rule_too_sensitive` | `alarm_rule_too_sensitive` |
| `environment_interference` | `environment_interference` |
| `sensor_noise` | `sensor_noise_or_stuck` |
| `verification_negative` | `verification_negative_false_alarm` |
| `duplicate_alarm_burst` | `duplicate_alarm_burst` |

### 2.1 Phase 4B 完成状态

| 项目 | 当前结论 |
|---|---|
| 新增样例 | `samples/devices/alarm_false_positive_cases.json` |
| 新增网关方法 | `query_alarm_rule` / `query_alarm_signal` / `query_alarm_environment` / `query_alarm_verification` / `query_alarm_correlation` |
| 新增工具 | `alarm__query_rule` / `alarm__query_signal` / `alarm__query_environment` / `alarm__query_verification` / `alarm__query_correlation` |
| 工具权限 | 全部 `READ_ONLY`，全部要求 `device:read` |
| 失败边界 | 参数非法、缺权限、设备或报警事实缺失时受控失败，不产生 EvidenceDraft |
| 后续衔接 | Phase 4C 再把报警 Evidence 纳入 CitationPolicy，并新增规则、报告和固定评测 |

## 3. Phase 4C：规则、报告与评测（待开始）

- [ ] 报警类 EvidenceType 纳入 CitationPolicy 设备事实集合；
- [ ] 新增 `application/alarm_diagnosis_rules.py`；
- [ ] 报告展示报警候选根因、证据链、排查顺序、排除项；
- [ ] 新增 `scripts/eval_phase4_alarm_false_positive.py`；
- [ ] Phase 4 eval 输出 JSON 和 Markdown；
- [ ] Phase 0/1/2/3 回归不退化。

### 3.1 候选标签

| 候选标签 | 判定依据 |
|---|---|
| `alarm_rule_too_sensitive` | 规则灵敏度高、阈值低或防抖时间过短 |
| `environment_interference` | 雨、雾、强光、风、夜间、阴影等环境干扰 |
| `sensor_noise_or_stuck` | 信号噪声过高、传感器抖动、卡死或信号缺失 |
| `verification_negative_false_alarm` | 复核未发现真实目标，疑似误报 |
| `duplicate_alarm_burst` | 短时间重复告警风暴或重复事件 |
| `insufficient_alarm_evidence` | 缺少关键报警事实 |

### 3.2 固定评测目标

| 指标 | 目标 |
|---|---:|
| total | >= 5 |
| passed | == total |
| label_accuracy | 1.0 |
| citation_compliance | 1.0 |
| sensitive_leak_count | 0 |
| external_model_called | false |

## 4. Phase 4 Definition of Done

- [x] Phase 4A 领域模型与测试完成；
- [x] Phase 4B 只读工具与样例案例完成；
- [ ] Phase 4C 规则、报告与固定评测完成；
- [ ] `label_accuracy == 1.0`；
- [ ] `citation_compliance == 1.0`；
- [ ] `sensitive_leak_count == 0`；
- [ ] `external_model_called == false`；
- [ ] Git 工作区干净并推送。
