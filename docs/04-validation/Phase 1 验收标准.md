# Phase 1 验收标准

> 验收对象：摄像头黑屏深化场景。Phase 1 只验证本地样例、只读工具、Evidence、CitationPolicy、报告和评测脚本，不接真实设备和真实模型。

## 1. 工程验收

- [ ] `uv run ruff check .` 通过；
- [ ] `uv run pytest` 通过；
- [ ] `uv run python scripts/demo_phase0_camera_black_screen.py` 仍可运行；
- [ ] `uv run python scripts/eval_phase1_camera_black_screen.py` 可运行；
- [ ] 不新增数据库、Alembic、SQLAlchemy；
- [ ] 不新增真实 LLM SDK；
- [ ] 不访问真实设备。

## 2. 业务场景验收

至少覆盖 4 个摄像头黑屏子案例：

| case_id | 必须性 | 期望候选根因 |
|---|---|---|
| `camera_offline` | 必须 | 设备离线或网络不可达 |
| `stream_publish_failed` | 必须 | 码流发布或编码异常 |
| `high_bitrate_encoder_timeout` | 必须 | 编码配置过高导致设备压力 |
| `platform_pull_failed` | 必须 | 平台侧接入或拉流链路异常 |
| `channel_offline` | 建议 | 通道绑定或平台接入异常 |

## 3. Evidence 验收

- [ ] 每个案例至少产生 3 条 Evidence；
- [ ] 新增摄像头事实必须进入 Evidence；
- [ ] Evidence 必须属于当前 Diagnosis；
- [ ] Evidence 引用必须通过 CitationPolicy；
- [ ] `possible` 不允许零引用；
- [ ] `probable` 至少引用两类设备事实 Evidence；
- [ ] `confirmed` 仍只能人工产生。

## 4. 评测验收

评测脚本至少输出：

- [ ] case_id；
- [ ] expected_label；
- [ ] actual_label；
- [ ] matched；
- [ ] evidence_count；
- [ ] cited_evidence_ids；
- [ ] external_model_called；
- [ ] summary；
- [ ] markdown report path。

最低指标：

| 指标 | 门槛 |
|---|---:|
| 固定案例运行成功率 | 100% |
| candidate_label 命中率 | ≥ 75% |
| Evidence 引用合规率 | 100% |
| 敏感信息泄露 | 0 |
| external_model_called | false |

## 5. 报告验收

- [ ] 报告显示候选根因标签；
- [ ] 报告显示证据链解释；
- [ ] 报告显示 cited evidence ids；
- [ ] 报告显示建议排查顺序；
- [ ] 报告说明为什么不是其他候选原因；
- [ ] 报告不包含未脱敏密码、Token、secret。

## 6. 禁止事项

- [ ] 不接真实摄像头；
- [ ] 不拉 RTSP；
- [ ] 不重启设备；
- [ ] 不下发配置；
- [ ] 不扩展门禁/报警完整闭环；
- [ ] 不引入 LangGraph 或多 Agent；
- [ ] 不把规则判断包装成 confirmed 根因。

## 7. 面试验收

完成 Phase 1 后，应能回答：

1. 同样是摄像头黑屏，系统如何区分不同根因候选？
2. 设备状态、通道、码流、平台拉流分别能证明什么？
3. 为什么设备离线或告警存在仍不能直接 confirmed？
4. 为什么 Phase 1 不急着接真实摄像头？
5. 规则判断和 LLM 推理是什么关系？
6. 如果真实企业落地，还需要接哪些系统？
