# Phase 11 开发总结与真机封版计划

## 1. 阶段结论

Phase 11A～11D 的工程与最小真机只读验收已完成。项目已从“本机 HTTP 契约 + 高保真 Simulator”推进到
严格只读 ONVIF Adapter、ONVIF/RTSP/平台/内容跨来源诊断和十轮稳定门禁。
授权 Wi-Fi 真机已完成发现、HTTP Digest 认证、Profile、Stream URI 与 RTSP Digest
OPTIONS 闭环；这仍不能表述为生产上线、长期真机稳定或真实诊断准确率验证。

## 2. 已形成的闭环

1. 资产目录和 Adapter Registry 确定来源；
2. ONVIF 采集设备、Profile 与 Stream URI 事实，RTSP 只做只读探活；
3. 平台契约提供拉流业务事实，FFmpeg 只对显式案例执行内容黑屏检测；
4. Tool Registry 执行只读工具，失败调用不生成 Evidence；
5. Application Service 落地 Evidence，CitationPolicy 约束引用；
6. 规则层只产生候选标签，`confirmed` 仍由 HumanReview 产生。

## 3. 验收证据

- 八场景矩阵：8/8；
- 跨来源完整 Agent Loop：4/4；
- 十轮稳定门禁：10/10，场景断言 80/80，Agent Loop 40/40；
- 全量测试：1633 passed；
- Ruff：通过；
- 每轮代理状态：前后均无 toxic 且 Proxy 已恢复；
- 停止后：无 Lab 容器、运行进程、临时凭证和运行时 ONVIF 配置；
- 外部模型调用：否；真实设备访问：否。

## 4. Phase 11D 真机最小验收

真机可通过 Wi-Fi 接入，只要电脑与摄像头处于同一局域网、关闭 AP/客户端隔离，
设备启用本地 ONVIF/RTSP，且本机能访问对应端口。建议为设备保留 DHCP 地址，
并优先使用 2.4 GHz Wi-Fi 完成首次稳定接入。

真机阶段只执行一次受控的发现、认证、Profile、Stream URI 与 RTSP 探活，不执行
PTZ、配置变更、重启或固件操作，不自动重试。真实 IP、序列号、用户名、密码、
完整 URI 和画面不得进入 Git、Evidence 或报告。

2026-09-17 已按上述边界完成授权真机验收。设备使用 HTTP Digest 访问 ONVIF，
RTSP 同样要求 Digest，且其 challenge 使用合法的无-qop 形式。最终只读探针发现
2 个 Profile，主码流为 H.265、3840×2160，认证后的 RTSP OPTIONS 成功。
详细脱敏证据与未验证边界见 `Phase 11D 真机验收记录`。

## 5. 封版原则

11D 通过后停止增加 Phase 和故障域。后续只允许修复真机兼容问题、P0/P1 缺陷与
文档错误，然后生成最终架构图、演示 Runbook、简历描述、三分钟讲解稿、面试追问
闭环和业务+代码教学材料。

## 6. 最终回归

- pytest：1810 passed，0 failed，0 error；
- Ruff 与 `git diff --check`：通过；
- Phase 0～6、8～10 固定脚本：通过；Phase 7 默认入口按设计拒绝真实模型调用；
- Device Lab：Verify 8/8、Agent Loop 4/4；Stability 10/10、80/80、40/40；
- Lab 停止后：容器、进程与运行时凭证残留均为 0。

最终审计见 `docs/05-audits/2026-09-18-Phase11最终封版审计.md`。
