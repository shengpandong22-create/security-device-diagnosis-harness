# Phase 11D 真机验收记录

## 1. 验收范围

验收日期：2026-09-17。

本次在用户明确授权下，对一台与开发机处于同一局域网的消费级 Wi-Fi 摄像头执行
最小只读联调。每轮均预先限定操作、交换次数和禁止事项；失败操作不自动重试。
报告只保留布尔值、计数、协议类别和非识别性媒体属性，不保存设备地址、序列号、
用户名、密码、完整 XAddr、Profile Token、Stream URI、challenge、原始 XML、响应头、
响应正文或画面。

## 2. 已验证链路

| 阶段 | 结果 | 证据边界 |
|---|---|---|
| WS-Discovery | 通过 | 仅用于进程内选择唯一授权设备，地址不持久化 |
| DeviceInformation | 通过 | HTTP Digest 认证成功；厂商、型号、固件仅记录“字段存在” |
| Profiles | 通过 | 发现 2 个 Profile，不保存名称或 Token |
| 主码流事实 | 通过 | H.265，3840×2160 |
| Stream URI | 通过 | URI 仅在进程内用于后续 RTSP 请求 |
| 未认证 RTSP OPTIONS | 符合预期 | TCP 与 RTSP 响应正常，设备要求 Digest |
| RTSP Digest OPTIONS | 通过 | 兼容无 qop challenge；认证后返回成功类别 |

最终脱敏结果为：`completed=true`、`rtsp_reachable=true`、
`authentication_scheme=digest`。最终闭环预算为 Profiles 最多 2 次 HTTP 交换、
Stream URI 最多 2 次 HTTP 交换、RTSP OPTIONS 最多 2 次交换，总计不超过 6 次；
实现在任何阶段均不包含自动重试。

## 3. 真机兼容修复

1. 私网 HTTP 必须显式开启，并继续受 host allowlist 约束；公网 HTTP 仍拒绝。
2. ONVIF 支持显式选择 WS-Security 或 HTTP Digest；Digest 必须声明两次交换预算。
3. SOAP 1.2 `action` 参数与兼容 `SOAPAction` header 同时发送。
4. Device 与 Media service path 来自已授权 XAddr 的受控结构，禁止 query、fragment 与
   路径穿越。
5. RTSP 失败不再折叠为单一布尔值，可区分连接、响应、认证与方法支持阶段。
6. RTSP Digest 支持 `qop=auth` 和合法的无-qop 形式，拒绝未知算法与 `auth-int`。
7. RTSP Digest 认证头只发送摘要，不发送明文密码；challenge 与完整 URI不进入报告。

## 4. 安全与隐私证据

- 所有操作均为读取或探活；未执行 PTZ、配置修改、重启、固件或录像写操作。
- 无外部 LLM、BGE、通知或第三方云服务调用。
- `input_tokens` / `output_tokens` 保持 `null`，没有伪造不可得指标。
- 真机输出位于被 `.gitignore` 排除的 `demo-output/`，Git 文档只记录脱敏结论。
- 自动化测试覆盖明文密码不进入 SOAP、RTSP 请求结果、异常与序列化结果。
- 用户曾在交互界面暴露临时密码；该密码不在仓库文件中，验收后必须轮换。

## 5. 未验证边界

本次不证明生产上线、线上准确率或长期稳定性，也未验证：

- 真机视频解码、逐帧黑屏检测、音频、录像检索、告警订阅与事件推送；
- PTZ、配置写入、重启、固件升级等写操作；
- 多厂商、多型号、多固件、多网络拓扑和公网/NAT 环境兼容性；
- 断网重连、密码轮换后的长期运行、并发访问和持续压力；
- 真实设备上的完整 Agent 诊断准确率与人工复核率。

Simulator 十轮稳定门禁与本次真机最小协议验收必须分别陈述，不得混算。
