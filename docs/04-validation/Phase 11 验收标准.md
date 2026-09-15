# Phase 11 验收标准

## 1. Phase 11A

- [x] ONVIF Adapter 只允许受控 endpoint 与 host allowlist
- [x] 凭证只由 CredentialResolverPort 在调用边界解析
- [x] DeviceInformation、Profiles、StreamUri、RTSP 探活可映射到既有领域对象
- [x] 认证、超时、不可用、非法响应和缺 Profile 为稳定受控错误
- [x] 异常、Evidence、日志与报告不含密码、Nonce、完整 URI 或原始 SOAP
- [x] 无 ONVIF/设备写操作，无自动重试或 fallback

## 2. Phase 11B

- [x] 四个跨来源案例进入正式 Runner、Registry、Gateway、Evidence、Citation 链路
- [x] RTSP 失败不误判为设备离线
- [x] 平台拉流失败能被设备侧正常事实排除设备离线候选
- [x] 部分事实不被伪造成确定事实，失败调用不生成 Evidence
- [x] 黑屏结论引用受控码流事实（其中同时包含健康状态与内容异常）
- [x] confirmed 仍只能由 HumanReview 产生

## 3. Phase 11C

- [x] 八场景连续十轮 80/80，无随机失败
- [x] 每轮前后 toxic 和 Proxy 状态均恢复
- [x] 全量 pytest、ruff、diff check 和安全扫描通过
- [x] 停止后无容器、进程、凭证和运行时 ONVIF 配置残留
- [x] 报告明确标记 simulator_stability，不冒充真机联调

稳定门禁实测：10/10 轮，场景断言 80/80，完整 Agent Loop 40/40；每轮
`before_clean=true` 且 `after_clean=true`，未调用外部模型或真实设备。
全量回归为 1633 passed；停止后 `contract/onvif/toxiproxy=false`，运行时凭证、
ONVIF 配置与状态文件残留数为 0，Device Lab 容器残留数为 0。

## 4. Phase 11D 与封版

- [ ] 用户明确授权设备、时间窗、只读范围和最大调用次数
- [ ] Wi-Fi 真机完成发现、认证、Profile 与 RTSP 最小联调
- [ ] 真机报告已脱敏并与 Simulator 报告分开
- [ ] 无写操作、无自动重试、无真实凭证入库或入 Git
- [ ] 完成最终面试材料与教学路线，项目停止功能堆砌
