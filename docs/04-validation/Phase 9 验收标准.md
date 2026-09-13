# Phase 9 验收标准

## 1. Phase 9A 接入契约与模拟器

- [x] DeviceAsset 不含真实凭证，ConnectionProfile 只保存 credential reference
- [x] LLM 与工具参数不能选择 endpoint、credential 或 Adapter
- [x] capability、deadline、权限和稳定错误分类可表达
- [x] Simulator 支持正常、离线、超时、认证失败、限流、能力缺失和非法响应
- [x] 故障注入完全确定性，不使用随机概率
- [x] Simulator 调用轨迹不保存凭证或原始敏感响应
- [x] 现有 StaticDeviceGateway 和 Phase 0～8 回归不受影响

## 2. Phase 9B HTTP Adapter

- [ ] 只允许 HTTPS 或本机 HTTP，拒绝 URL userinfo、查询凭证和重定向
- [ ] endpoint 来自受控配置和 host allowlist，工具参数不能覆盖
- [ ] 凭证只由 CredentialResolverPort 提供且不进入异常、日志、Evidence、审计和报告
- [ ] 连接/读取/总超时、响应大小、JSON Schema 和列表数量均有限制
- [ ] 401/403、超时、429、5xx、非法 JSON 和 Schema 错误映射为稳定错误类型
- [ ] 失败响应不产生 Evidence，异常原文不回传 LLM
- [ ] 所有 HTTP 操作只读且默认不自动重试
- [ ] 本机契约服务和 Adapter 集成测试通过

## 3. Phase 9C 路由与 Runtime

- [ ] device_id 通过 AssetCatalog 确定性映射 Adapter 与 capabilities
- [ ] 模型不能伪造或覆盖路由结果
- [ ] 缺资产、禁用资产、缺 capability 或 Adapter 未就绪均受控失败
- [ ] supported fault types 根据已装配能力生成，不手工放开全集
- [ ] Static、Simulator、HTTP Adapter 产生相同领域对象和 Evidence 契约
- [ ] 部分事实、超时和限流进入受控降级，不猜测根因
- [ ] 正式 Runtime 至少一个故障域完成非 Static Adapter 闭环

## 4. Phase 9D 可观测与影子评测

- [ ] 指标覆盖工具、Adapter、Evidence、状态、预算、成本、P0 和人工反馈
- [ ] 指标标签不含设备 ID、IP、人员、车牌、卡号、Token、endpoint 或自由文本
- [ ] simulator_e2e 执行真实 Runner、Registry、Gateway、Evidence、Citation 和 Review
- [ ] simulator_e2e 与 real_model、authorized_device_e2e 分开报告
- [ ] 端到端结果接入 Phase 8 历史、失败归因和 Phase 7 Gate
- [ ] 影子模式不写设备、不自动关闭工单、不自动发送外部通知
- [ ] 人工反馈只进入候选区，不自动修改正式数据集

## 5. 真实联调条件

- [ ] 用户明确授权测试环境、设备范围、只读凭证、时间窗和最大调用次数
- [ ] 每次真实联调前完整预算预检，失败不自动重试
- [ ] 原始响应经过字段白名单和脱敏后才可成为 Evidence
- [ ] 报告不包含凭证、真实设备标识、完整 endpoint 或原始响应
- [ ] 若没有真实环境授权，本节保持未完成，不能用 Simulator 勾选

## 6. 质量门禁

- [x] `uv run ruff check .` 通过
- [x] `uv run pytest` 全量通过且无删除/跳过既有安全测试
- [x] Phase 0～8 固定 Demo/Eval 全部回归
- [x] 新增 P0 安全反例覆盖异常到 LLMRequest 的完整链路
- [x] `git diff --check` 通过
- [x] 仓库无数据库、设备响应、`.env`、密钥、证书或真实凭证残留

## 7. 能力表述

- [x] Simulator 结果明确标注为高保真模拟
- [ ] 真实 HTTP 契约测试不表述为真实设备准确率
- [ ] 小样本授权联调不表述为生产稳定性或生产准确率
- [ ] 只有真实 Adapter + 完整 Agent Loop 才可称为端到端设备诊断评测
- [ ] Phase 9 完成后只称“具备企业试接入能力”，生产化仍需规模、权限、高可用和长期反馈验证
