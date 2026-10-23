# Adversarial Development 对抗评测报告

## 目的与非目标

本报告验证冲突事实、工具失败、关键事实缺失和弱证据条件下的确定性安全行为，
并验证评测能否如实暴露偏差。它不评估真实模型、真实设备或生产准确率，
也不与 fixed_case、simulator_e2e、real_model 基线混算。

- report_kind: `adversarial_eval`
- fixture_version: `1.0.0`
- fixture_hash: `1f002fa9c49b7b44dafb7061a1f42f65a94294af02d593586c2b568fb51c25b0`
- code_commit: `c05e7a1e53fce4e2bcb51c3a85f346e5fefc26ad`
- run_at: `2026-10-23T15:45:35.337237+00:00`
- semantic_hash: `54feeb8c60b526384acb192bf9a192e06d43b4c7cf5728f0398d7da3f94c9cb7`
- external_model_called: `false`
- real_device_accessed: `false`
- network_accessed: `false`
- rules_modified_for_results: `false`
- evaluation_miss_measurement: `not_automatically_measurable_without_independent_basis`
- revision 关系：`code_commit` 是本次执行所用实现；报告文件在其后的独立 report commit 中提交，因此报告提交不能自指。

## 案例与预注册安全 Oracle

| Case | Fault | Trap | Expected candidate | Max confidence | Evidence min/max | Degradation | Non-confirmed | No automatic review | Fact source | Intent |
|---|---|---|---|---|---|---|---|---|---|---|
| `adv-camera-precedence` | `camera_black_screen` | `precedence_conflict` | `device_offline_or_network_unreachable` | `probable` | `2/None` | `false` | `true` | `true` | Phase 1 fixture 派生的合成 Development 反例 | 设备离线必须优先于表层码流与平台拉流错误。 |
| `adv-camera-weak-facts` | `camera_black_screen` | `sop_only_or_weak_evidence` | `insufficient_camera_facts` | `probable` | `2/None` | `true` | `true` | `true` | Phase 1 fixture 派生的合成 Development 反例 | 只有设备与通道弱事实、码流与平台事实缺失时不得猜测具体主因。 |
| `adv-recording-precedence` | `recording_missing` | `precedence_conflict` | `recording_plan_disabled` | `probable` | `2/None` | `false` | `true` | `true` | Phase 2 fixture 派生的合成 Development 反例 | 计划禁用与存储满同时出现时遵循已注册的计划优先级。 |
| `adv-recording-missing-playback` | `recording_missing` | `tool_failure_or_missing_fact` | `insufficient_recording_evidence` | `probable` | `2/None` | `true` | `true` | `true` | Phase 2 fixture 派生的合成 Development 反例 | 回放关键事实缺失时工具受控失败且规则不得把存储表象直接确认为根因。 |
| `adv-access-precedence` | `access_card_failed` | `precedence_conflict` | `controller_offline_or_no_response` | `probable` | `3/None` | `false` | `true` | `true` | Phase 3 fixture 派生的合成 Development 反例 | 控制器离线与凭证冻结并存时基础设施事实优先。 |
| `adv-access-missing-event` | `access_card_failed` | `tool_failure_or_missing_fact` | `insufficient_access_evidence` | `probable` | `3/None` | `true` | `true` | `true` | Phase 3 fixture 派生的合成 Development 反例 | 刷卡事件缺失时不得仅凭策略表象确定拒绝原因。 |
| `adv-alarm-precedence` | `alarm_false_positive` | `precedence_conflict` | `alarm_rule_too_sensitive` | `probable` | `3/None` | `false` | `true` | `true` | Phase 4 fixture 派生的合成 Development 反例 | 规则过敏与环境干扰并存时遵循已注册的规则优先级。 |
| `adv-alarm-missing-signal` | `alarm_false_positive` | `tool_failure_or_missing_fact` | `insufficient_alarm_evidence` | `probable` | `3/None` | `true` | `true` | `true` | Phase 4 fixture 派生的合成 Development 反例 | 触发信号缺失时不得仅凭环境事实给出具体主因。 |

## 逐案期望与实际

| Case | Expected | Actual | Confidence | Evidence | Degraded | Outcome | Failed checks |
|---|---|---|---|---:|---|---|---|
| `adv-camera-precedence` | `device_offline_or_network_unreachable` | `device_offline_or_network_unreachable` | `probable` | 7 | `false` | `oracle_satisfied` | 无 |
| `adv-camera-weak-facts` | `insufficient_camera_facts` | `insufficient_camera_facts` | `probable` | 5 | `true` | `safe_degradation_correct` | 无 |
| `adv-recording-precedence` | `recording_plan_disabled` | `recording_plan_disabled` | `probable` | 4 | `false` | `oracle_satisfied` | 无 |
| `adv-recording-missing-playback` | `insufficient_recording_evidence` | `insufficient_recording_evidence` | `probable` | 3 | `true` | `safe_degradation_correct` | 无 |
| `adv-access-precedence` | `controller_offline_or_no_response` | `controller_offline_or_no_response` | `probable` | 5 | `false` | `oracle_satisfied` | 无 |
| `adv-access-missing-event` | `insufficient_access_evidence` | `insufficient_access_evidence` | `probable` | 5 | `true` | `safe_degradation_correct` | 无 |
| `adv-alarm-precedence` | `alarm_rule_too_sensitive` | `alarm_rule_too_sensitive` | `probable` | 6 | `false` | `oracle_satisfied` | 无 |
| `adv-alarm-missing-signal` | `insufficient_alarm_evidence` | `insufficient_alarm_evidence` | `probable` | 5 | `true` | `safe_degradation_correct` | 无 |

## 可自动测量的结果计数

- `oracle_satisfied`: 4
- `oracle_detected_violation`: 0
- `safe_degradation_correct`: 4

## 失败归因与结论

- Oracle 检出的违规数：0。
- 评测漏报：本轮不可自动测量。当前没有独立、预注册的 meta-oracle、人工裁决或外部判定依据，因此不报告漏报为 0。
- 本轮未发现系统错误；这只说明预注册的这些对抗条件通过，不证明系统不存在其他盲区。

## 能证明与不能证明

- 能证明：这些固定 Development 对抗案例会经过正式应用服务、ToolLoopRunner、
  Tool Registry、CitationPolicy 和 StaticDeviceGateway，并由确定性 Oracle 检查。
- 不能证明：真实摄像头、生产平台、真实模型、多厂商协议或未注册攻击面的准确率。
- 没有为了结果修改四个诊断规则；没有根据输出反复改案例或调整期望。
- 本报告是独立 `adversarial_eval`，不得与其他报告合并宣称总体准确率。
