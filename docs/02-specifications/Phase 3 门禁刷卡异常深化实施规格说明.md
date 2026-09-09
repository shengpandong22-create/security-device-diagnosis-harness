# Phase 3 门禁刷卡异常深化实施规格说明

> 阶段目标：把 Harness 从视频/录像类设备故障扩展到门禁业务域。  
> 实施方式：仍采用“先领域事实、再只读工具、再规则/报告/评测”的三段式推进。  
> 关键边界：不做开门、授权、下发卡权限等写操作；所有结论仍必须经过 Evidence、CitationPolicy 和 HumanReview。

## 1. 为什么 Phase 3 选择门禁刷卡异常

Phase 0～2 已经完成两个视频相关场景：

```text
摄像头黑屏
录像缺失 / 录像异常
```

如果 Phase 3 继续做视频链路，项目会越来越像“摄像头诊断工具”。门禁刷卡异常能把诊断对象扩展到另一个典型安防业务域：

```text
人 / 卡 / 门 / 控制器 / 权限 / 时段 / 刷卡事件
```

它能证明当前 Harness 不是绑定某一种设备，而是一套可扩展的安防诊断约束框架。

## 2. Phase 3 总体闭环

Phase 3 的完整闭环应当是：

```text
用户描述：某人刷卡不开门 / 门禁刷卡失败
  -> 只读工具采集门禁事实
  -> 生成 Evidence
  -> 规则推断候选根因
  -> CitationPolicy 校验引用可信度
  -> 报告展示证据链和排查顺序
  -> 人工确认 / 驳回 / 继续调查
  -> 固定案例集评测
```

## 3. Phase 3 拆分

### 3.1 Phase 3A：门禁领域模型

只做领域建模，不接工具、不改 Agent Loop、不新增 API。

建议新增：

| 模型 | 说明 |
|---|---|
| `AccessControllerSnapshot` | 门禁控制器在线状态、健康状态、最近错误 |
| `DoorSnapshot` | 门状态、锁状态、门磁状态、是否常开/常闭 |
| `CredentialSnapshot` | 卡、人脸或凭证状态、是否冻结、是否过期 |
| `AccessPolicySnapshot` | 人员/凭证对门的权限、允许时段、有效期 |
| `AccessEvent` | 刷卡事件、拒绝原因、事件时间、读卡器方向 |

建议枚举：

| 枚举 | 取值建议 |
|---|---|
| `AccessControllerStatus` | online / offline / degraded |
| `DoorStatus` | closed / open / forced_open / held_open / unknown |
| `LockStatus` | locked / unlocked / jammed / unknown |
| `CredentialType` | card / face / fingerprint / pin |
| `CredentialStatus` | active / frozen / expired / lost / unknown |
| `AccessDecision` | granted / denied / timeout / no_response |
| `AccessDenyReason` | credential_invalid / permission_denied / time_window_denied / controller_offline / door_lock_error / anti_passback / unknown |

验收重点：

- 领域模型只依赖标准库与 pydantic；
- 凭证号、人脸特征、PIN、Token、secret 等不得原样保存；
- 事件时间、门、人员/凭证、权限关系能表达；
- 模型只表达事实，不表达诊断结论；
- 不新增 API、工具、样例、评测。

### 3.2 Phase 3B：只读工具与样例案例

目标：让 Harness 可以通过只读工具采集门禁事实。

建议新增 DeviceGateway 方法：

| 方法 | 返回模型 |
|---|---|
| `query_access_controller(device_id)` | `AccessControllerSnapshot` |
| `query_door(device_id, door_id)` | `DoorSnapshot` |
| `query_credential(credential_id)` | `CredentialSnapshot` |
| `query_access_policy(person_id, door_id)` | `AccessPolicySnapshot` |
| `search_access_events(device_id, door_id, credential_id, limit)` | `list[AccessEvent]` |

建议新增只读工具：

| 工具 | 说明 |
|---|---|
| `access__query_controller` | 查询门禁控制器状态 |
| `access__query_door` | 查询门/锁/门磁状态 |
| `access__query_credential` | 查询卡、人脸等凭证状态 |
| `access__query_policy` | 查询人员/凭证是否有该门权限 |
| `access__search_events` | 查询近期刷卡事件 |

固定样例建议至少 5 个：

| case_id | 典型根因 |
|---|---|
| `credential_frozen` | 凭证被冻结 |
| `permission_denied` | 无该门权限 |
| `time_window_denied` | 当前时间不在授权时段 |
| `controller_offline` | 门禁控制器离线 |
| `door_lock_jammed` | 门锁异常或卡滞 |

### 3.3 Phase 3C：规则、报告和固定评测

目标：形成和 Phase 1、Phase 2 一样的完整闭环。

建议新增：

- `application/access_diagnosis_rules.py`
- `scripts/eval_phase3_access_card_failed.py`
- `docs/04-validation/Phase 3 验收标准.md`

候选标签建议：

| label | 说明 |
|---|---|
| `credential_invalid_or_frozen` | 凭证无效、冻结、挂失或过期 |
| `permission_not_granted` | 无该门权限 |
| `access_time_window_denied` | 授权时段不覆盖刷卡时间 |
| `controller_offline_or_no_response` | 控制器离线或无响应 |
| `door_lock_or_sensor_issue` | 门锁、门磁或门状态异常 |
| `insufficient_access_evidence` | 缺少关键门禁事实 |

验收指标：

| 指标 | 目标 |
|---|---|
| `total` | >= 5 |
| `passed` | == total |
| `label_accuracy` | 1.0 |
| `citation_compliance` | 1.0 |
| `sensitive_leak_count` | 0 |
| `external_model_called` | false |

## 4. CitationPolicy 扩展口径

Phase 3C 才允许把门禁类 EvidenceType 纳入 `DEVICE_FACT_EVIDENCE_TYPES`。

建议新增 EvidenceType：

| EvidenceType | 来源 |
|---|---|
| `access_controller` | 控制器状态 |
| `access_door` | 门/锁/门磁状态 |
| `access_credential` | 凭证状态 |
| `access_policy` | 权限策略 |
| `access_event` | 刷卡事件 |

Phase 3A / 3B 不应提前修改 CitationPolicy，避免模型结论可信度规则和业务规则脱节。

## 5. 不做什么

Phase 3 不做：

- 远程开门；
- 修改人员权限；
- 下发卡、人脸或指纹；
- 修改门禁时段；
- 控制器重启；
- 真实门禁设备接入；
- 数据库持久化；
- 前端页面；
- RAG；
- 真实模型自动评测。

这些能力要么属于高风险写操作，要么属于企业化落地阶段，不能混入当前只读诊断 Harness。

## 6. 面试表达重点

Phase 3 完成后，可以这样解释项目演进：

> 我没有只做摄像头链路，而是把 Harness 扩展到了门禁刷卡异常。门禁场景涉及人、凭证、门、控制器、授权时段和刷卡事件，和视频链路完全不同。系统仍然坚持只读工具采集事实，所有事实落成 Evidence，候选根因由规则和 LLM 在 Harness 内推理，`probable` 必须引用足够的设备事实，`confirmed` 只能来自人工确认。这样证明这个项目不是某个场景的脚本，而是一套可以迁移到不同安防业务域的诊断框架。

## 7. 下一步

下一步先执行 Phase 3A：门禁领域模型。

Phase 3A 不做工具、不做样例、不做报告和评测，只把门禁诊断所需事实表达清楚。

