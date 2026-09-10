# Phase 4 报警误报深化实施规格说明

> 阶段目标：把 Harness 扩展到报警误报 / 误触发诊断。  
> 实施方式：仍采用“先领域事实、再只读工具、再规则/报告/评测”的三段式推进。  
> 关键边界：只做只读诊断，不自动关闭报警、不自动改规则、不自动布防/撤防。

## 1. 为什么 Phase 4 选择报警误报

Phase 1～3 已经覆盖三类安防设备运维问题：

```text
摄像头黑屏：实时画面不可用
录像缺失：历史录像不可用
门禁刷卡异常：通行链路失败
```

报警误报代表另一类问题：

```text
系统产生了告警，但告警不一定是真的。
```

这类问题不能简单靠“有没有报警事件”判断，而要综合：

- 报警规则是否过于敏感；
- 传感器 / 算法信号是否稳定；
- 是否存在雨、雾、强光、风等环境干扰；
- 视频或人工复核是否发现真实目标；
- 短时间是否出现重复告警风暴；
- 相邻设备是否也发生关联告警。

因此 Phase 4 的价值在于验证 Harness 能否处理“事件可信度判断”，而不只是处理“设备故障定位”。

## 2. Phase 4 总体闭环

```text
用户描述：某设备频繁报警 / 疑似误报
  -> 只读工具采集报警规则、信号、环境、复核、关联事实
  -> 生成 Evidence
  -> CitationPolicy 校验引用可信度
  -> 规则推断候选根因
  -> 报告展示证据链、排查顺序和排除项
  -> 人工确认 / 驳回 / 继续调查
  -> 固定案例集评测
```

核心原则仍然不变：

- LLM 可以推理，但不能越过 Harness；
- 工具只能只读；
- Evidence 必须可追踪；
- `probable` 必须引用至少两类设备事实；
- `confirmed` 只能来自人工确认；
- 固定案例评测必须能回归。

## 3. Phase 4 拆分

### 3.1 Phase 4A：报警误报领域模型

只做领域建模，不接工具、不改 Agent Loop、不新增 API。

建议新增 `domain/alarm.py`。

| 模型 | 说明 |
|---|---|
| `AlarmRuleSnapshot` | 报警规则类型、启用状态、灵敏度、阈值、防抖时间、布防时段 |
| `AlarmSignalSnapshot` | 触发时信号值、阈值、噪声等级、是否稳定 |
| `AlarmEnvironmentSnapshot` | 天气、光照、风、雨、雾、夜间等环境干扰事实 |
| `AlarmVerificationSnapshot` | 视频复核 / 人工复核结果，是否发现真实目标 |
| `AlarmCorrelationSnapshot` | 短时间重复告警次数、相邻设备关联告警数量、是否孤立事件 |

建议枚举：

| 枚举 | 取值建议 |
|---|---|
| `AlarmType` | motion / intrusion / line_crossing / tamper / access_abnormal / unknown |
| `AlarmSeverityLevel` | info / warning / critical |
| `AlarmRuleSensitivity` | low / medium / high |
| `AlarmSignalStatus` | stable / noisy / stuck / missing / unknown |
| `EnvironmentInterferenceType` | none / rain / fog / strong_light / wind / night / shadow / unknown |
| `VerificationResult` | target_found / no_target_found / inconclusive / not_checked |
| `CorrelationPattern` | isolated / burst / multi_device / unknown |

验收重点：

- 领域模型只依赖标准库与 pydantic；
- 模型只表达事实，不表达结论；
- `sensitivity=high` + 阈值过低、防抖过短可被表达；
- `noise_level`、`signal_status`、`verification_result`、`correlation_pattern` 可被表达；
- 摄像头画面、人员信息、真实设备地址、Token、secret 等敏感内容不得原样保存；
- 不新增 API、Tool、Runner、DeviceGateway、样例和 eval。

### 3.2 Phase 4B：只读工具与样例案例

目标：让 Harness 可以通过只读工具采集报警误报相关事实。

建议新增 DeviceGateway 方法：

| 方法 | 返回模型 |
|---|---|
| `query_alarm_rule(device_id, rule_id)` | `AlarmRuleSnapshot` |
| `query_alarm_signal(device_id, alarm_id)` | `AlarmSignalSnapshot` |
| `query_alarm_environment(device_id, alarm_id)` | `AlarmEnvironmentSnapshot` |
| `query_alarm_verification(device_id, alarm_id)` | `AlarmVerificationSnapshot` |
| `query_alarm_correlation(device_id, alarm_id)` | `AlarmCorrelationSnapshot` |

建议新增只读工具：

| 工具 | 说明 |
|---|---|
| `alarm__query_rule` | 查询报警规则配置 |
| `alarm__query_signal` | 查询触发时信号与噪声 |
| `alarm__query_environment` | 查询环境干扰事实 |
| `alarm__query_verification` | 查询复核结果 |
| `alarm__query_correlation` | 查询重复与关联告警 |

固定样例至少 5 个：

| case_id | 典型根因 |
|---|---|
| `rule_too_sensitive` | 规则灵敏度过高或阈值过低 |
| `environment_interference` | 雨、雾、强光、风等环境干扰 |
| `sensor_noise` | 传感器噪声、抖动或卡死 |
| `verification_negative` | 视频/人工复核未发现目标 |
| `duplicate_alarm_burst` | 短时间重复告警风暴 |

### 3.3 Phase 4C：规则、报告和固定评测

目标：形成与 Phase 1～3 一致的完整闭环。

建议新增：

- `application/alarm_diagnosis_rules.py`
- `scripts/eval_phase4_alarm_false_positive.py`
- `docs/04-validation/Phase 4 验收标准.md`

候选标签建议：

| label | 说明 |
|---|---|
| `alarm_rule_too_sensitive` | 规则灵敏度过高、阈值过低或防抖过短 |
| `environment_interference` | 环境干扰导致误触发 |
| `sensor_noise_or_stuck` | 传感器噪声、抖动、卡死或信号异常 |
| `verification_negative_false_alarm` | 复核未发现真实目标，疑似误报 |
| `duplicate_alarm_burst` | 短时间重复告警风暴 |
| `insufficient_alarm_evidence` | 缺少关键报警事实 |

评测指标：

| 指标 | 目标 |
|---|---|
| `total` | >= 5 |
| `passed` | == total |
| `label_accuracy` | 1.0 |
| `citation_compliance` | 1.0 |
| `sensitive_leak_count` | 0 |
| `external_model_called` | false |

## 4. CitationPolicy 扩展口径

Phase 4C 才允许把报警类 EvidenceType 纳入 `DEVICE_FACT_EVIDENCE_TYPES`。

建议新增 EvidenceType：

| EvidenceType | 来源 |
|---|---|
| `alarm_rule` | 报警规则配置 |
| `alarm_signal` | 报警触发信号 |
| `alarm_environment` | 环境干扰事实 |
| `alarm_verification` | 复核结果 |
| `alarm_correlation` | 重复/关联告警 |

这些 EvidenceType 属于设备/现场事实，可以支撑 `probable`，但仍然必须至少引用两类不同事实。

## 5. 不做什么

Phase 4 不做：

- 自动关闭报警；
- 自动确认误报；
- 自动修改报警阈值；
- 自动下发布防/撤防策略；
- 自动屏蔽设备；
- 接真实报警主机或真实摄像头；
- 数据库持久化；
- 前端页面；
- RAG；
- 真实模型自动评测。

原因：报警误报可能关联真实安全事件，当前阶段只能做只读诊断和候选判断，不能引入写操作。

## 6. 面试表达重点

Phase 4 完成后，可以这样解释项目演进：

> 前面几个 Phase 更多是在定位“系统确实不可用”的问题，Phase 4 开始处理“系统产生了事件，但事件是否可信”的问题。报警误报不能只看告警本身，需要同时看规则配置、传感器信号、环境干扰、复核结果和关联告警。我的设计仍然坚持 Harness 约束：LLM 只负责推理，事实采集、Evidence、引用校验、人工确认和评测都由确定性代码负责。

## 7. 下一步

下一步执行 Phase 4A：报警误报领域模型。

完成 4A 后，再进入：

```text
Phase 4A 领域模型
  -> Phase 4B 只读工具 + 样例案例
  -> Phase 4C 规则推断 + 报告 + 固定评测
```
