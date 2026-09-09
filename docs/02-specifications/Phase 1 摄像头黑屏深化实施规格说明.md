# Phase 1 摄像头黑屏深化实施规格说明

> 阶段目标：把 Phase 0 的单一摄像头黑屏 demo，深化为可解释、可评测、可面试展示的安防设备诊断样板场景。

## 1. Phase 1 的定位

Phase 0 已经证明 Harness 闭环成立：

```text
设备故障 -> 只读工具 -> Evidence -> CitationPolicy -> 人工确认 -> Markdown 报告
```

Phase 1 不追求扩展更多业务域，而是把“摄像头黑屏”讲深。

核心问题从：

```text
摄像头黑屏，系统能不能给一个候选结论？
```

升级为：

```text
同样是摄像头黑屏，系统能不能根据不同设备事实组合，区分设备离线、通道异常、码流异常、配置过高、平台拉流失败等不同根因候选？
```

## 2. 不做什么

Phase 1 明确不做：

- 不扩展门禁刷卡异常；
- 不扩展报警误报；
- 不扩展录像缺失完整闭环；
- 不接真实摄像头；
- 不拉 RTSP 视频流；
- 不重启设备；
- 不下发配置；
- 不接数据库；
- 不接真实模型；
- 不引入 LangGraph、多 Agent、向量数据库、工单系统。

本阶段仍然坚持：

```text
本地样例数据 + Fake LLM + 只读工具 + Evidence/Citation/Review
```

## 3. Phase 1 建议拆分

### 3.1 Phase 1A：摄像头事实模型深化

目标：让摄像头黑屏不再只依赖 `DeviceSnapshot.stream_status`，而是有更细的事实结构。

建议新增领域模型：

| 模型 | 建议文件 | 说明 |
|---|---|---|
| `ChannelSnapshot` | `domain/camera.py` | 通道在线、通道号、绑定设备、平台接入状态 |
| `StreamSnapshot` | `domain/camera.py` | 主/子码流、编码格式、码率、分辨率、帧率、取流状态 |
| `PlatformPullStatus` | `domain/camera.py` | 平台拉流状态、错误码、最近失败时间 |

可暂不新增 `RecordingPlanSnapshot`，避免 Phase 1 与录像缺失场景混在一起。录像计划可以作为 Phase 2 或 Phase 1 后续扩展。

建议枚举：

| 枚举 | 值 |
|---|---|
| `ChannelStatus` | `online`、`offline`、`unknown` |
| `PullStatus` | `success`、`failed`、`timeout`、`unknown` |
| `StreamKind` | `main`、`sub` |

验收要求：

- 这些模型只表达设备事实，不表达诊断结论；
- 领域层仍只依赖标准库和 Pydantic；
- 敏感字段继续脱敏；
- 新增模型必须有单元测试。

### 3.2 Phase 1B：StaticDeviceGateway 数据结构升级

目标：让本地 JSON 样例能表达多个摄像头黑屏子案例。

建议新增样例文件：

```text
samples/devices/camera_black_screen_cases.json
```

最少包含 4 个设备案例：

| device_id | case_id | 事实组合 | 期望根因候选 |
|---|---|---|---|
| `cam-offline-01` | `camera_offline` | 设备离线 | 设备离线或网络不可达 |
| `cam-channel-offline-01` | `channel_offline` | 设备在线但通道离线 | 通道绑定或平台接入异常 |
| `cam-stream-failed-01` | `stream_publish_failed` | 设备在线、通道在线、码流发布失败 | 码流发布或编码异常 |
| `cam-high-bitrate-01` | `high_bitrate_encoder_timeout` | 高码率 + 编码超时告警 | 配置过高导致编码压力 |
| `cam-platform-pull-01` | `platform_pull_failed` | 设备侧正常但平台拉流失败 | 平台侧接入或拉流链路异常 |

说明：表中列了 5 个，最低验收 4 个；如果时间允许建议实现 5 个，面试表达更完整。

StaticDeviceGateway 可以扩展新方法，也可以通过现有 `read_config_snapshot` / `query_status` 的 `extra` 字段承载，但我更建议新增清晰 Port 方法：

```python
query_channel_snapshot(device_id: str) -> ChannelSnapshot
query_stream_snapshot(device_id: str, stream_kind: StreamKind = StreamKind.MAIN) -> StreamSnapshot
query_platform_pull_status(device_id: str) -> PlatformPullStatus
```

验收要求：

- 文件不存在、JSON 非法、设备不存在仍然受控失败；
- 新字段解析有测试；
- 旧 `static_devices.sample.json` 仍可用于 Phase 0 demo，不被破坏；
- 新样例不包含真实设备 IP、账号、密码、Token。

### 3.3 Phase 1C：摄像头黑屏诊断策略深化

目标：不是增加 LLM 自由度，而是增强 Harness 的事实解释能力。

建议新增：

```text
src/security_diagnosis_harness/application/camera_diagnosis_rules.py
```

它不是替代 LLM，而是提供确定性辅助判断，用于：

- 根据 Evidence 组合生成候选原因标签；
- 为报告输出“为什么不是其他原因”；
- 为评测提供 expected label 对齐。

建议规则：

| 事实组合 | candidate_label | 解释 |
|---|---|---|
| 设备 `online=false` | `device_offline_or_network_unreachable` | 设备离线或网络不可达 |
| 设备在线 + 通道离线 | `channel_binding_or_platform_access_issue` | 通道绑定、平台接入或通道状态异常 |
| 通道在线 + 码流 failed/timeout | `stream_publish_or_encoder_issue` | 码流发布失败或编码器异常 |
| 高码率/高分辨率 + encoder timeout | `overloaded_encoding_configuration` | 编码配置过高导致设备压力 |
| 设备/通道/码流正常 + 平台拉流失败 | `platform_pull_or_access_path_issue` | 平台侧拉流或接入链路异常 |

验收要求：

- 规则只基于 Evidence / 设备事实，不读取外部状态；
- 规则输出只能作为候选解释，不能直接 confirmed；
- Phase 1 后 probable 结论建议至少引用两类设备事实 Evidence；
- 如果只有 SOP，没有设备事实，只能 possible。

### 3.4 Phase 1D：评测脚本与报告增强

目标：把 demo 变成固定案例集回归。

建议新增脚本：

```text
scripts/eval_phase1_camera_black_screen.py
```

输出：

```text
demo-output/phase1-camera-black-screen-eval.json
demo-output/phase1-camera-black-screen-eval.md
```

评测维度：

| 维度 | 判定 |
|---|---|
| expected_label 是否命中 | 必须 |
| 是否至少引用 1 条 Evidence | 必须 |
| probable 是否引用设备事实 | 必须 |
| 是否生成 next_steps | 必须 |
| 是否误判 confirmed | 必须不能 |
| 是否泄露敏感配置 | 必须不能 |

建议最低通过标准：

- 4 个固定案例全部运行成功；
- label 命中率 ≥ 75%；
- Evidence 引用合规率 100%；
- 敏感信息泄露 0；
- external_model_called=false。

报告增强建议：

- 增加“证据链解释”；
- 增加“候选根因标签”；
- 增加“排除项：为什么不是其他原因”；
- 增加“建议排查顺序”。

## 4. API 是否要扩展

Phase 1 可以暂不增加新 API。

优先选择：

```text
样例数据 + ApplicationService + demo/eval 脚本
```

如果必须通过 API 暴露，也只允许新增只读查询字段，不新增写操作。

## 5. LLM 使用策略

Phase 1 默认仍然使用 Fake LLM。

允许后续单独增加低频真实模型验证脚本，但必须满足：

- 不在自动测试中调用；
- 需要显式环境变量开启；
- 一次最多调用固定少量案例；
- 不发送真实设备凭证；
- 不自动重试。

Phase 1 的质量不依赖真实模型通过，而依赖固定案例集和规则/Evidence/Citation 的稳定性。

## 6. 文件建议

推荐新增或修改：

```text
src/security_diagnosis_harness/domain/camera.py
src/security_diagnosis_harness/ports/device_gateway.py
src/security_diagnosis_harness/adapters/device_gateway/static.py
src/security_diagnosis_harness/tools/device_channel.py
src/security_diagnosis_harness/tools/device_stream.py
src/security_diagnosis_harness/tools/platform_pull.py
src/security_diagnosis_harness/application/camera_diagnosis_rules.py
src/security_diagnosis_harness/application/reports.py
samples/devices/camera_black_screen_cases.json
scripts/eval_phase1_camera_black_screen.py
docs/04-validation/Phase 1 验收标准.md
```

如果实现压力较大，可以先不新增三个工具，而是让已有设备工具输出更丰富 payload。但长期看，新增工具更利于 Tool Registry 展示。

## 7. 测试要求

至少新增：

| 测试类别 | 要求 |
|---|---|
| 领域测试 | 摄像头事实模型创建、枚举合法性、脱敏 |
| Adapter 测试 | 新样例数据加载、多案例解析、设备不存在失败 |
| Tool 测试 | channel / stream / platform pull 工具产生 EvidenceDraft |
| 规则测试 | 5 类事实组合对应不同 candidate_label |
| 应用服务测试 | Phase 0 demo 仍可运行，Phase 1 多案例可运行 |
| 报告测试 | 证据链解释、排除项、敏感信息不泄露 |
| 评测脚本测试 | 输出 JSON/MD，统计指标正确 |

## 8. 验收命令

```powershell
uv run ruff check .
uv run pytest
uv run python scripts/demo_phase0_camera_black_screen.py
uv run python scripts/eval_phase1_camera_black_screen.py
```

## 9. Phase 1 Definition of Done

- [x] Ruff 全绿；
- [x] Pytest 全绿（253 个测试）；
- [x] Phase 0 demo 仍可运行；
- [x] Phase 1 eval 脚本可运行；
- [x] 至少 4 个摄像头黑屏子案例（实际 5 个）；
- [x] 每个案例都有不同或可解释的 candidate_label；
- [x] probable 至少引用两类设备事实 Evidence；
- [x] possible 不允许零引用；
- [x] confirmed 仍只能人工产生；
- [x] 不调用真实模型；
- [x] 不访问真实设备；
- [x] 不新增数据库；
- [x] 不泄露真实凭证；
- [x] 生成 Phase 1 验收记录（见 `docs/04-validation/Phase 1 验收标准.md`）。

## 10. 给其他模型的边界提示

可以直接给其他模型这段：

```text
你现在只实现 Phase 1 摄像头黑屏深化，不扩展门禁、报警、录像缺失，不接真实设备，不接数据库，不调用真实模型。

目标是让摄像头黑屏从单一 demo 深化为多子场景评测：
设备离线、通道异常、码流发布失败、配置过高、平台拉流失败。

所有新增事实仍必须进入 Evidence，所有候选结论仍必须经过 CitationPolicy。
probable 至少引用两类设备事实 Evidence。
confirmed 仍只能由人工 review 产生。
```

## 11. 面试表达

Phase 1 做完后，项目表达可以升级为：

> 我没有停留在“摄像头黑屏 -> 大模型回答”的 Demo，而是把黑屏拆成设备离线、通道异常、码流异常、配置过高和平台拉流失败等子场景。系统通过只读设备工具采集状态、告警、配置、通道、码流和平台拉流事实，统一转成 Evidence，再由 CitationPolicy 控制结论可信度。这样同样是黑屏，系统能根据不同事实组合给出不同根因候选和排查顺序。
