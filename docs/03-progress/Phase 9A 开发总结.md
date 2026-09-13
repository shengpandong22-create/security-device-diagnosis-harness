# Phase 9A 开发总结

## 1. 完成范围

Phase 9A 完成供应商无关设备接入契约、确定性高保真 Simulator，以及首条
`simulator_e2e` 固定评测。现有 StaticDeviceGateway 与 Phase 0～8 评测入口不变。

## 2. 关键边界

- LLM 和工具只接收业务参数，不能选择 endpoint、凭证或 Adapter；
- ConnectionProfile 只保存 endpoint alias 与 credential reference；
- 解析后的凭证使用 SecretStr 包装，只能在 Adapter 边界短暂解包；
- Simulator 故障由场景显式配置，不使用随机概率或真实等待；
- 调用轨迹不含 device_id、调用参数、原始响应或自由文本；
- 模拟失败映射为稳定错误类型，失败结果不产生 Evidence；
- confirmed 仍只能由 HumanReview 产生。

## 3. 固定场景

当前固定评测覆盖成功、设备离线事实、超时、认证失败、限流、能力缺失和
非法响应。其中端到端脚本执行成功、状态超时、码流非法响应三个场景；其余
错误分类由参数化契约测试覆盖。

## 4. 运行方式

```powershell
uv run python scripts/eval_phase9a_simulator_e2e.py
```

报告种类固定为 `simulator_e2e`。它执行真实 Runner、Registry、Gateway、
Evidence、CitationPolicy 与 HumanReview，但仍使用 FakeLLM 和本地样例，不能
表述为真实模型或真实设备诊断准确率。

## 5. 下一步

Phase 9B 先实现本机 HTTP 契约服务，再实现单一供应商无关、严格只读且默认
不重试的 HTTP Adapter。没有用户明确授权时，不访问真实设备或平台。
