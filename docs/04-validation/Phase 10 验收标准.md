# Phase 10 验收标准

## Phase 10A

- [x] simulator_e2e 强类型运行身份与聚合摘要
- [x] 不可比控制变量拒绝进入同一趋势
- [x] P0、安全泄漏、失败 Evidence 和核心退化阻塞 Gate
- [x] History 原子写入且不保存案例原文与设备标识

验收证据：固定脚本完成 baseline → candidate → 逐项 Gate；History 加载时重算
可比性指纹、内容哈希与 Gate 结论，拒绝格式合法但被篡改的历史。

## Phase 10B

- [x] 授权清单不含 endpoint、凭证或真实设备标识
- [x] 缺授权、过期、超预算在调用前受控失败
- [x] 只读 allowlist 且没有自动重试

10B-1 已完成纯离线授权契约与不可变预算预检；正式 Runtime 接线留在 10B-2。

## Phase 10C

- [x] authorization_dry_run 与 authorized_device_e2e 明确分离
- [x] 试接入 Runbook 与真实报告空模板齐备
- [x] Phase 0～10 全量回归和安全审计通过
- [x] 未访问真实设备、真实模型、BGE 或外部网络

最终证据：全量 pytest 与 ruff 通过；两个 Phase 10 固定脚本均退出 0；仓库没有
数据库残留或真实凭证文件。本阶段未启用任何真实 HTTP Adapter。
