# Phase 11 ONVIF 跨来源诊断与稳定封版实施规格

## 1. 阶段目标

Phase 11 不增加新的安防故障域。目标是把 Device Lab 中已经可重复制造的
ONVIF、RTSP、网络和平台事实接入正式 Harness，证明同一诊断可以基于多来源事实
形成可追踪候选结论，并通过稳定性门禁。

完成 11A～11C 后称为“核心功能稳定、具备真机试接入条件”；11D 必须在用户明确
授权并提供设备后执行，Simulator 结果不得替代真机结果。11D 完成后项目功能封版，
后续以文档、演示和面试训练为主。

## 2. Phase 11A：严格只读 ONVIF Adapter

- 使用受控 endpoint、host allowlist 与 credential reference；
- 支持 WS-Security UsernameToken PasswordDigest；
- 只实现 GetDeviceInformation、GetProfiles、GetStreamUri 与 RTSP 只读探活；
- 输出既有 DeviceSnapshot、ChannelSnapshot、StreamSnapshot 契约；
- 认证、超时、不可用、非法 XML、缺 Profile 映射为稳定错误类别；
- 不把 URL userinfo、密码、Nonce、原始 SOAP 或完整 Stream URI写入异常和 Evidence；
- 不实现 PTZ、改配置、重启、时间同步等写操作；
- 不自动重试，不做 Adapter fallback。

## 3. Phase 11B：跨来源完整 Agent Loop

将 ONVIF/RTSP 设备事实与 Security Platform HTTP 事实组合，但仍由资产目录确定性
路由。第一批闭环：

1. ONVIF 在线、RTSP 不可达；
2. 设备和 RTSP 正常、平台拉流失败；
3. 主码流正常、子码流缺失；
4. 协议与码流正常、视频内容持续纯黑。

失败调用不得生成 Evidence；部分事实不得补全为确定事实；候选结论必须引用真实
Evidence，`confirmed` 仍只能由 HumanReview 产生。

## 4. Phase 11C：十轮稳定门禁

- 八场景矩阵连续运行 10 轮，共 80 次场景断言全部通过；
- 每轮清除 toxic 并恢复 Proxy，不共享上一轮故障状态；
- 错误分类、Evidence 类型和候选标签保持确定性；
- 全量 pytest、ruff、安全扫描通过；
- 启停后无进程、容器、凭证和运行时配置残留；
- 生成独立 `simulator_stability` 报告，不表述为真实设备准确率。

## 5. Phase 11D：授权真机联调与封版

仅允许同局域网 Wi-Fi 或有线设备，只读调用且单案例不自动重试。联调验证发现、
认证、Profile、RTSP 与厂商异常差异；不执行 PTZ、改配置或重启。报告必须去除真实
IP、序列号、用户名、密码、视频画面和完整 URI。

11D 完成后停止新增 Phase，整理最终架构图、演示 Runbook、简历描述、三分钟讲解稿、
追问闭环和逐课教学材料。

