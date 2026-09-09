# Phase 1 验收标准

> 验收对象：摄像头黑屏深化场景。Phase 1 只验证本地样例、只读工具、Evidence、CitationPolicy、报告和评测脚本，不接真实设备和真实模型。

## 1. 工程验收

- [x] `uv run ruff check .` 通过；
- [x] `uv run pytest` 通过（253 个测试）；
- [x] `uv run python scripts/demo_phase0_camera_black_screen.py` 仍可运行；
- [x] `uv run python scripts/eval_phase1_camera_black_screen.py` 可运行；
- [x] 不新增数据库、Alembic、SQLAlchemy；
- [x] 不新增真实 LLM SDK；
- [x] 不访问真实设备。

## 2. 业务场景验收

已覆盖 5 个摄像头黑屏子案例（最低要求 4 个）：

| case_id | 必须性 | 期望候选根因 | 实测 |
|---|---|---|---|
| `camera_offline` | 必须 | 设备离线或网络不可达 | 命中 |
| `stream_publish_failed` | 必须 | 码流发布或编码异常 | 命中 |
| `high_bitrate_encoder_timeout` | 必须 | 编码配置过高导致设备压力 | 命中 |
| `platform_pull_failed` | 必须 | 平台侧接入或拉流链路异常 | 命中 |
| `channel_offline` | 建议 | 通道绑定或平台接入异常 | 命中 |

## 3. Evidence 验收

- [x] 每个案例至少产生 3 条 Evidence（实测 7 条）；
- [x] 新增摄像头事实必须进入 Evidence（channel / stream / platform_pull）；
- [x] Evidence 必须属于当前 Diagnosis；
- [x] Evidence 引用必须通过 CitationPolicy；
- [x] `possible` 不允许零引用；
- [x] `probable` 至少引用两类设备事实 Evidence；
- [x] `confirmed` 仍只能人工产生。

## 4. 评测验收

评测脚本已输出：

- [x] case_id；
- [x] expected_label；
- [x] actual_label；
- [x] matched；
- [x] evidence_count；
- [x] cited_evidence_ids；
- [x] external_model_called；
- [x] summary；
- [x] markdown report path。

最低指标：

| 指标 | 门槛 |
|---|---:|
| 固定案例运行成功率 | 100% |
| candidate_label 命中率 | ≥ 75% |
| Evidence 引用合规率 | 100% |
| 敏感信息泄露 | 0 |
| external_model_called | false |

## 5. 报告验收

- [x] 报告显示候选根因标签；
- [x] 报告显示证据链解释；
- [x] 报告显示 cited evidence ids；
- [x] 报告显示建议排查顺序；
- [x] 报告说明为什么不是其他候选原因；
- [x] 报告不包含未脱敏密码、Token、secret。

## 6. 禁止事项

- [x] 不接真实摄像头；
- [x] 不拉 RTSP；
- [x] 不重启设备；
- [x] 不下发配置；
- [x] 不扩展门禁/报警完整闭环；
- [x] 不引入 LangGraph 或多 Agent；
- [x] 不把规则判断包装成 confirmed 根因。

## 6.1 落地位置

| 内容 | 落点 |
|---|---|
| 摄像头事实模型 | `domain/camera.py` |
| 枚举 | `ChannelStatus` / `StreamKind` / `PullStatus` |
| 新增 EvidenceType | `device_channel`、`device_stream`、`platform_pull`（均纳入设备事实集合） |
| 样例数据 | `samples/devices/camera_black_screen_cases.json`（5 个案例） |
| Gateway 扩展 | `query_channel_snapshot` / `query_stream_snapshot` / `query_platform_pull_status` |
| 新增只读工具 | `device__query_channel`、`device__query_stream`、`platform__query_pull_status` |
| 候选根因规则 | `application/camera_diagnosis_rules.py` |
| 评测脚本 | `scripts/eval_phase1_camera_black_screen.py` |

## 6.2 规则判定顺序

| 顺序 | 条件 | candidate_label |
|---|---|---|
| R1 | 设备 `online=false` | `device_offline_or_network_unreachable` |
| R2 | 在线 + 通道离线或未注册平台 | `channel_binding_or_platform_access_issue` |
| R3 | 在线 + 通道正常 + `ENCODER_TIMEOUT` + 高码率/高分辨率 | `overloaded_encoding_configuration` |
| R4 | 在线 + 通道正常 + 码流 failed/timeout | `stream_publish_or_encoder_issue` |
| R5 | 设备/通道/码流正常 + 平台拉流 failed/timeout | `platform_pull_or_access_path_issue` |
| R6 | 其他（事实正常或不足） | `insufficient_camera_facts` |

R3 排在 R4 之前：编码配置过高是更具体的解释，避免把"高码率导致的编码超时"
笼统归到码流异常。

## 6.3 实测评测结果

```text
total: 5
passed: 5
label_accuracy: 1.0
citation_compliance: 1.0
external_model_called: false
sensitive_leak_count: 0
```

每个案例 7 条 Evidence：device_status、device_channel、device_stream、
platform_pull、device_alarm、device_config、knowledge_sop。

## 7. 面试验收

完成 Phase 1 后，应能回答：

1. 同样是摄像头黑屏，系统如何区分不同根因候选？
2. 设备状态、通道、码流、平台拉流分别能证明什么？
3. 为什么设备离线或告警存在仍不能直接 confirmed？
4. 为什么 Phase 1 不急着接真实摄像头？
5. 规则判断和 LLM 推理是什么关系？
6. 如果真实企业落地，还需要接哪些系统？
