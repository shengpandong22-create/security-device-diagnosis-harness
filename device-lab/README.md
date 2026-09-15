# 本机安防设备诊断实验室

这套实验室把三类可重复验证能力拆开：

1. `onvif-simulator v0.4.0` 提供虚拟 ONVIF 设备协议端点；
2. `Toxiproxy v2.12.0` 注入延迟、超时、断连等网络故障；
3. 项目现有 FastAPI 契约服务提供设备离线、码流异常、平台拉流失败等业务事实。

它不是实物摄像机验证，也不能冒充生产环境联调。启动脚本会用固定 FFmpeg
容器生成一段本地 H.264 纯黑视频，供 ONVIF Simulator 循环输出。

## 运行

在仓库根目录执行：

```powershell
./scripts/device_lab.ps1 Bootstrap
./scripts/device_lab.ps1 Start
./scripts/device_lab.ps1 Verify
./scripts/device_lab.ps1 Status
./scripts/device_lab.ps1 Stop
```

`Start` 会生成仅存在于 `.device-lab/` 的一次性契约凭证；该目录已被 Git 忽略。
`Verify` 先执行基础网络闭环，再运行八场景矩阵。报告保存在被 Git 忽略的
`.device-lab/runtime/scenario-report.json`。脚本退出前会恢复所有 Proxy 并移除 toxic；
`Stop` 会关闭宿主机进程、删除 Docker 容器，并删除含一次性凭证的运行时配置。

## 八场景矩阵

| 场景 | 真实注入/验证方式 | 预期结果 |
| --- | --- | --- |
| ONVIF 正常、RTSP 不可达 | 关闭 `onvif-rtsp` Proxy | SOAP 正常、RTSP 代理失败、RTSP 上游正常 |
| ONVIF 认证失败 | 错误 WS-Security PasswordDigest | 错误凭证拒绝、一次性正确凭证通过 |
| 主码流正常、子码流缺失 | 运行时只注册 `profile_main` | main Profile 和 RTSP 存在，sub 不存在 |
| 设备在线、平台拉流失败 | 查询固定契约案例 | 在线为真，平台拉流状态为 failed |
| Wi-Fi 间歇性高延迟 | 确定性交替启停 latency toxic | `ok/timeout/ok/timeout` |
| 请求中连接重置 | `reset_peer` toxic | Adapter 稳定分类为 `unavailable` |
| 平台返回部分事实 | 显式开启的 Lab-only 路由 | 缺失事实保持 `None/unknown`，不伪造 |
| 协议和码流健康但视频纯黑 | 从代理 RTSP 抽帧并运行 `blackdetect` | 持续黑色视频被识别 |

## 固定端口

| 端口 | 用途 |
| --- | --- |
| `28082` | FastAPI 契约服务上游 |
| `28081` | 经 Toxiproxy 的契约服务 |
| `28083` | ONVIF Simulator HTTP 上游 |
| `28080` | 经 Toxiproxy 的 ONVIF HTTP |
| `28555` | ONVIF Simulator RTSP 上游（未做媒体内容验收） |
| `28554` | 经 Toxiproxy 的 RTSP（未做媒体内容验收） |
| `28474` | Toxiproxy 控制 API |

## 供应链与安全边界

- ONVIF Simulator Windows 包固定为 `v0.4.0`，下载后校验 SHA-256；
- Toxiproxy 镜像固定 tag 与 digest；
- FFmpeg `7.1-alpine` 镜像固定 tag 与 digest，只生成并分析本地媒体夹具；
- 不读取 `.env`，不提交凭证、真实 IP、账号、Token 或设备数据；
- 不包含设备写操作；模拟器并非 ONVIF 官方认证设备。
