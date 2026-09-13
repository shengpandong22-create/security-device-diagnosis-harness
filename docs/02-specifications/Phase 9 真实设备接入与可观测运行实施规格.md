# Phase 9 真实设备接入与可观测运行实施规格

## 1. 阶段定位

Phase 0～8 已完成四类安防故障的领域表达、只读工具、证据与人工确认、知识沉淀、持久化、安全治理和评测运营。但正式 Runtime 仍只支持摄像头黑屏，并使用 StaticDeviceGateway + FakeLLM。Phase 9 的目标不是继续增加故障类型，而是证明 Harness 面对高保真或真实只读设备接口时仍能保持权限、证据、脱敏、预算和评测边界。

Phase 9 完成前，项目仍是工程化诊断原型；完成高保真端到端影子评测后，可以称为“具备企业试接入能力”，但仍不能称为生产系统。

## 2. 核心原则

1. 设备状态、告警和配置是 Evidence，不是自动 confirmed 结论；
2. LLM 不选择网络地址、凭证、厂商 Adapter 或租户；
3. Adapter 路由由资产目录和能力目录确定性决定；
4. 第一阶段所有设备操作均为 READ_ONLY；
5. 凭证只在 Adapter 调用边界短暂存在，不进入 Domain、日志、Evidence、审计或报告；
6. 真实网络访问默认关闭，自动测试只访问进程内 Fake 或本机模拟服务；
7. 不因接入真实设备而放松 Tool Registry、CitationPolicy、人工 confirmed 和预算限制；
8. 高保真模拟器通过不等于真实设备通过，二者必须分开报告。

## 3. 总体架构

```text
诊断请求
  → SecurityDiagnosisApplicationService
  → ToolLoopRunner
  → ToolRegistry（权限 / 故障域 / 预算）
  → RoutedDeviceGateway
       → Device Asset Catalog
       → Capability Catalog
       → CredentialResolverPort
       → Adapter Router（确定性）
            ├─ StaticDeviceGateway（既有离线回归）
            ├─ SimulatorDeviceGateway（高保真故障注入）
            └─ SecurityPlatformHttpAdapter（单一真实只读入口）
  → ToolExecutionResult 统一脱敏
  → DiagnosisEvidence / CitationPolicy
  → 候选结论
  → HumanReview confirmed

旁路：结构化运行指标 → ObservabilityPort → 本地/OTel Adapter
      脱敏运行轨迹 → Phase 7/8 Grader、历史、趋势与发布门禁
```

## 4. Phase 9A：设备接入契约与高保真模拟器

### 4.1 领域与 Port

新增供应商无关对象：

- `DeviceAsset`：内部资产 ID、设备类型、站点/区域别名、Adapter key、启用状态；
- `DeviceCapability`：状态、通道、码流、告警、配置、录像、门禁、报警等只读能力；
- `DeviceConnectionProfile`：协议、endpoint alias、credential reference、超时策略；不得包含密码、Token 或 URL userinfo；
- `DeviceRequestContext`：request ID、diagnosis ID、deadline、调用来源和权限；
- `DeviceAdapterError` 分类：authentication、timeout、rate_limited、unavailable、unsupported_capability、invalid_response；
- `DeviceAssetCatalogPort`、`CredentialResolverPort`、`ObservabilityPort`。

不让 LLM 看到 credential reference、endpoint 或 Adapter key。工具仍只接收业务参数，例如 device_id、channel_id 和时间窗。

### 4.2 高保真模拟器

模拟器不能只是返回静态 JSON，应支持确定性场景：

- 正常响应；
- 设备离线；
- 超时；
- 认证失败；
- 限流；
- 部分能力缺失；
- 非法/缺字段响应；
- 响应延迟；
- 同一资产不同通道差异。

所有故障由 fixture 明确配置，不使用随机概率。每次运行记录调用次数、能力、耗时和错误类别，但不记录凭证或原始敏感响应。

### 4.3 9A 明确不做

- 不访问公网或真实设备；
- 不实现写配置、重启设备、开关布防等动作；
- 不开放正式 Runtime 的四个故障域；
- 不接真实 LLM；
- 不引入消息队列、Kubernetes 或微服务拆分。

## 5. Phase 9B：单一只读 Security Platform HTTP Adapter

### 5.1 接入目标

优先实现一个供应商无关的安防平台 OpenAPI Adapter，而不是同时接多家设备协议。先由本机高保真 HTTP 服务实现契约，再在用户明确提供已授权测试环境后执行一次真实联调。

第一批只读能力：

- 查询设备在线状态；
- 查询通道/码流状态；
- 查询指定时间窗告警；
- 读取经过字段白名单过滤的配置摘要；
- 可选：录像计划、存储状态与回放检查。

### 5.2 HTTP 安全边界

- 仅允许 HTTPS；本机模拟器可使用 localhost HTTP；
- 禁止 URL userinfo、查询串凭证和自动重定向；
- endpoint 必须来自受控配置并命中 host allowlist，工具参数不能覆盖；
- 显式连接、读取和总超时；
- 限制响应体大小、JSON 深度和列表数量；
- 2xx 也必须执行 Schema 校验，缺字段不能伪造成 Evidence；
- 认证信息通过 CredentialResolverPort 注入 Header，不进入异常文本；
- 401/403、408/超时、429、5xx 和协议错误映射为稳定错误类别；
- 不自动重试非明确幂等请求；Phase 9 全部请求均为只读，但默认仍不重试，避免隐藏真实失败与消耗预算。

### 5.3 真实联调授权

真实设备或平台调用必须由用户明确提供：测试环境地址、允许设备范围、只读账号/Token、调用时间窗和最大调用次数。凭证只通过进程环境或安全存储读取，不写 `.env`、命令历史、报告或 Git。

## 6. Phase 9C：确定性路由与正式 Runtime 接入

### 6.1 RoutedDeviceGateway

路由键来自 DeviceAssetCatalog：`device_id → adapter_key → capabilities`。禁止模型、Prompt 或工具参数指定 Adapter。路由前完成：

1. 资产存在性；
2. 资产启用状态；
3. 所需 capability；
4. 调用权限；
5. deadline；
6. Adapter 健康与配置状态。

### 6.2 能力驱动的故障域开放

正式 Runtime 的 supported fault types 不再手工扩大为四域全集，而是根据已装配工具、Adapter capabilities 和固定自检结果生成。某故障域缺少必要能力时，创建或运行诊断必须受控拒绝。

### 6.3 受控降级

- Adapter 不可用：返回 infrastructure/unavailable，不生成 Evidence；
- 单项能力缺失：进入 waiting_for_input 或 inconclusive，不猜测设备事实；
- 部分 Evidence 可用：可信度和引用继续由 CitationPolicy 决定；
- 超时或限流：不由 Agent 自动循环重试；
- Static、Simulator 和 HTTP 结果必须使用相同领域对象与 Evidence 类型。

## 7. Phase 9D：可观测性与端到端影子评测

### 7.1 最小指标

- 工具调用次数、成功率、错误类别和耗时分位；
- 各 Adapter、capability 和故障域的受控降级率；
- Evidence 产出率、缺失率与 Citation 合规率；
- 诊断完成、waiting_for_input、inconclusive、确认和驳回比例；
- 模型调用次数、Token、估算成本和预算阻断数；
- P0 安全 Finding 数；
- 人工确认耗时和候选结论采纳率。

指标标签禁止出现 device_id、人员、卡号、车牌、IP、Token、完整 endpoint 或自由文本，避免高基数和敏感泄漏。

### 7.2 两类端到端评测

1. `simulator_e2e`：执行真实 ToolLoopRunner、Registry、RoutedDeviceGateway、Simulator、Evidence、CitationPolicy 和 HumanReview；
2. `authorized_device_e2e`：仅在用户明确授权后，以极小案例和调用预算执行真实 HTTP Adapter。

两类报告不得合并。Phase 7D 的 `real_model` 结构化决策基线继续单独保留，也不能冒充这两类端到端结果。

### 7.3 影子模式

真实联调只输出候选诊断和建议，不执行设备写操作、不自动关闭工单、不触发通知升级。人工运维独立处理后，再记录脱敏的采纳/驳回结果作为 Phase 8 数据候选，不自动写回正式数据集。

## 8. 开发顺序

1. 9A-1：资产、能力、连接配置和错误分类；
2. 9A-2：高保真 Simulator 与故障注入；
3. 9A-3：Simulator 端到端固定评测；
4. 9B-1：本机 HTTP 契约服务；
5. 9B-2：只读 HTTP Adapter、安全边界与契约测试；
6. 9C-1：资产目录和确定性 Adapter Router；
7. 9C-2：能力驱动 Runtime 与受控降级；
8. 9D-1：结构化指标与本地 Observability Adapter；
9. 9D-2：端到端影子评测、Phase 8 历史与 Gate 接入；
10. 9D-3：经用户授权后再执行最小真实平台联调。

## 9. 安全红线

- 禁止提交真实 IP、账号、密码、Token、证书私钥、人脸、车牌、卡号和截图 URL；
- 禁止将底层 HTTP/SDK 异常原文发给 LLM；
- 禁止由模型选择 endpoint、credential 或 Adapter；
- 禁止将失败调用包装成 Evidence；
- 禁止自动 confirmed；
- 禁止自动重试真实模型、设备写请求或人工 Review；
- 禁止把 Simulator 满分或小样本真实联调描述为生产准确率；
- 禁止为接入真实设备绕过 Registry、fault-type guard、CitationPolicy、审计或预算。

## 10. 完成定义

Phase 9 完成必须同时满足：高保真 Simulator 端到端闭环；单一 HTTP Adapter 契约验证；确定性能力路由；正式 Runtime 受控开放至少一个真实接入故障域；结构化无敏感指标；Phase 7/8 回归不退化；用户授权下的真实联调报告（若无授权则明确保持未验收，不得用模拟器替代）。
