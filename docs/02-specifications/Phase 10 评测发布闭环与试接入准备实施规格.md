# Phase 10 评测发布闭环与试接入准备实施规格

## 1. 定位

Phase 10 不增加故障域，目标是把 Phase 9 的设备接入能力变成可比较、可发布、可授权
试接入的工程闭环。Simulator、真实模型和授权设备报告继续严格分离。

## 2. Phase 10A：Shadow History 与 Gate

- 为 `simulator_e2e` 定义独立的强类型运行身份、聚合摘要与可比性指纹；
- 历史只保存聚合指标，不保存设备 ID、原始 Evidence、Prompt 或响应；
- 只有场景集、配置、Adapter 类型等控制变量一致时才允许比较；
- P0、安全泄漏、失败 Evidence 或核心指标退化必须阻塞 Gate；
- 不把 Phase 7 的 `real_model` 指标强行混入设备 E2E 历史。

## 3. Phase 10B：授权联调清单与预算预检

- 定义不含凭证的授权清单：环境别名、设备范围别名、时间窗、允许操作与调用上限；
- 默认关闭，缺授权或预算不足时在任何 HTTP/设备调用前失败；
- 只允许只读操作，不自动重试、不自动通知、不自动 confirmed；
- 清单只引用 credential reference，不保存真实 endpoint 或密钥。

## 4. Phase 10C：试接入运行包与演练

- 输出操作手册、失败恢复、证据留存、人工确认和回滚步骤；
- 使用 Simulator 完成授权协议 dry-run，但明确标记 `authorization_dry_run`；
- 提供真实联调报告空模板，不生成或伪造真实设备结果；
- 完成 Phase 0～10 回归与严格审计。

## 5. 红线

禁止真实设备、真实模型、BGE、外部网络、设备写操作、自动重试、自动通知、自动 push；
禁止把 dry-run 或 Simulator 称为真实联调；禁止读取 `.env` 或提交任何凭证与真实标识。
