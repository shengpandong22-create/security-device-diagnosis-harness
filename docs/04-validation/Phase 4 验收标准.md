# Phase 4 验收标准

> 验收对象：报警误报 / 误触发诊断深化。  
> Phase 4 分三段：4A 领域模型、4B 只读工具与样例案例、4C 规则与评测。  
> 本文档不接真实设备、不接真实模型、不接数据库。

## 1. Phase 4A：报警误报领域模型（待开始）

### 1.1 工程验收

- [ ] `uv run ruff check .` 通过；
- [ ] `uv run pytest` 通过；
- [ ] Phase 0 demo 仍可运行；
- [ ] Phase 1 eval 仍 5/5；
- [ ] Phase 2 eval 仍 5/5；
- [ ] Phase 3 eval 仍 5/5；
- [ ] 领域模型只依赖标准库与 pydantic；
- [ ] 不新增 API、Tool、Runner、DeviceGateway 改动、样例 JSON、eval 脚本；
- [ ] 不新增数据库 / SQLAlchemy / Alembic / 真实模型 SDK；
- [ ] 不提交真实设备 IP、账号、密码、Token、邮箱授权码、真实视频截图。

### 1.2 事实模型验收

| 需要表达的事实 | 模型 | 验收要求 |
|---|---|---|
| 报警规则配置 | `AlarmRuleSnapshot` | 可表达启用状态、报警类型、灵敏度、阈值、防抖时间、布防时段 |
| 触发信号 | `AlarmSignalSnapshot` | 可表达信号值、阈值、噪声等级、信号状态 |
| 环境干扰 | `AlarmEnvironmentSnapshot` | 可表达雨、雾、强光、风、夜间、阴影等干扰 |
| 复核结果 | `AlarmVerificationSnapshot` | 可表达视频/人工复核是否发现真实目标 |
| 关联告警 | `AlarmCorrelationSnapshot` | 可表达短时间重复告警、相邻设备关联告警、孤立事件 |

### 1.3 安全与一致性验收

- [ ] 摄像头画面 URL、人员信息、真实设备地址、Token、secret 不得原样保存；
- [ ] `extra` 中凭证类字段统一脱敏为 `***REDACTED***`；
- [ ] 空 device_id / alarm_id / rule_id 应被拒绝；
- [ ] 布防时间段可以表达跨天；
- [ ] 模型不包含 `confidence` / `root_cause` / `conclusion` 等结论字段；
- [ ] 模块源码不含 `fastapi` / `sqlalchemy` / `alembic` / `openai` / `httpx` / `requests`。

## 2. Phase 4B：只读工具与样例案例（待开始）

- [ ] 新增报警类 DeviceGateway 只读方法；
- [ ] StaticDeviceGateway 支持报警误报样例数据；
- [ ] 新增 READ_ONLY 报警工具；
- [ ] 所有报警工具要求 `device:read`；
- [ ] 工具失败不产生 EvidenceDraft；
- [ ] 固定案例至少覆盖 5 类典型报警误报。

建议固定案例：

| case_id | 期望候选根因 |
|---|---|
| `rule_too_sensitive` | `alarm_rule_too_sensitive` |
| `environment_interference` | `environment_interference` |
| `sensor_noise` | `sensor_noise_or_stuck` |
| `verification_negative` | `verification_negative_false_alarm` |
| `duplicate_alarm_burst` | `duplicate_alarm_burst` |

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

- [ ] Phase 4A 领域模型与测试完成；
- [ ] Phase 4B 只读工具与样例案例完成；
- [ ] Phase 4C 规则、报告与固定评测完成；
- [ ] `label_accuracy == 1.0`；
- [ ] `citation_compliance == 1.0`；
- [ ] `sensitive_leak_count == 0`；
- [ ] `external_model_called == false`；
- [ ] Git 工作区干净并推送。
