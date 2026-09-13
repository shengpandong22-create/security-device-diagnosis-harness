# Phase 9 开发总结

## 1. 阶段结论

Phase 9 已完成“企业试接入”所需的工程基线：供应商无关设备契约、高保真
Simulator、本机 HTTP 契约 Adapter、资产目录与确定性路由、能力驱动 Runtime、
受控降级，以及低基数可观测影子评测。

这不代表真实设备联调或生产可用。`simulator_e2e`、`real_model` 与未来经授权的
`authorized_device_e2e` 是三类独立证据，禁止合并宣传准确率。

## 2. 核心链路

```text
Diagnosis Service -> ToolLoopRunner -> ToolRegistry
  -> RoutedDeviceGateway -> AssetCatalog + AdapterRegistry
  -> Static / Simulator / Local HTTP Adapter
  -> Evidence -> CitationPolicy -> Candidate -> HumanReview
```

模型不能选择 endpoint、凭证或 Adapter。失败调用映射为稳定 failure kind，不产生
Evidence、不回传底层异常，也不自动重试。`confirmed` 仍只有 HumanReview 能产生。

## 3. 验证证据

- `uv run ruff check .`：通过；
- `uv run pytest -q`：全量通过；
- Phase 0～4 固定案例均为 5/5，citation 1.0，敏感泄漏 0；
- Phase 6 一致性、乐观锁、重启和备份恢复脚本通过；
- Phase 7 数据集：6 个案例，Dev/Validation/Test 各 2，无跨集合泄漏；
- Phase 8 标注、发布和失败归因协议脚本通过；
- Phase 9A `simulator_e2e`：3/3 完成，失败 Evidence 违规 0；
- Phase 9C 能力与降级探针通过，无自动设备重试；
- Phase 9D 影子评测：3/3 完成，P0=0，设备写入=0，外部通知=0。

## 4. 严格审计结论

本轮修复了两个 P1：Registry 成功路径漏返回值；参数校验异常可能回显
Pydantic `input_value`。扩展回归覆盖 tools、agent、application、runtime，全量测试通过。
指标标签采用白名单并拒绝敏感值、高基数标识和自由文本。

## 5. 明确未完成

- 未访问或联调任何真实安防设备；
- 未执行 `authorized_device_e2e`；
- Phase 8 历史与 Gate 尚未正式接入 Phase 9D 报告；
- 未验证规模化并发、高可用、多租户权限和长期线上反馈；
- 未执行设备写操作、自动关闭工单或外部通知。

下一阶段应优先完成经授权的小规模只读联调和 Phase 9D 与历史/Gate 的正式接线，
而不是继续增加故障域或工具数量。
