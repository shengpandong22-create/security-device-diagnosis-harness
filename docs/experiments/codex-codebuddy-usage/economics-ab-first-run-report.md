# Codex / CodeBuddy 协作经济性首次 A/B 实验报告

## 结论

本轮两组都没有达到预先定义的成功门槛，因此**不能计算或宣称 cost per success
节省率**。协作组的 Codex 审核 token 明显低于 Codex 独立实现，但审核发现两个 P1；
独立实现组也存在真实入口兼容缺陷。当前证据不支持把双 Agent 编排设为默认开发方式。

按照实验前止损规则，本轮不通过返修制造成功样本，不追加任何模型调用。

## 固定条件

| 条件 | 值 |
| --- | --- |
| base commit | `f3d428f94942f59f8d301fc36436c6c97b02e0b3` |
| Codex model | `gpt-5.6-luna` |
| CodeBuddy model | `deepseek-v4.1-flash` |
| Codex session | 每次全新会话 |
| CodeBuddy max turns | 36 |
| 外部网络 / 设备 / BGE / 真实业务模型 | 均未访问 |

## A 组：Codex 独立实现

任务：Codex usage 原始日志完整性绑定。

实测 usage 来自 Codex 本地原始 session JSONL：

| 指标 | 实测值 |
| --- | ---: |
| input tokens | 741,517 |
| cached input tokens | 694,784 |
| uncached input tokens | 46,733 |
| output tokens | 12,469 |
| reasoning output tokens（output 子集，不重复计入总量） | 3,532 |
| input + output | 753,986 |

三条批准验收命令均退出 0，但不判成功：

1. 新实现只识别 `--model`，没有兼容现有调用使用的 `-m`；probe 没覆盖真实入口；
2. `Invoke-MeasuredCodex` 仍依赖仅由完整 orchestrator 定义的 `Write-TextFile`；
3. Codex 沙箱无法写主仓库的 worktree Git 元数据，提交失败；宿主没有代替修复或提交，
   避免把额外、无法计量的 Codex 工作混入 A 组。

结果：`failed_quality_gate`，只形成 `cost per attempt`。

## B 组：CodeBuddy 实现 + Codex 审核

任务：Protocol HANDOFF 与实际 diff、批准命令严格绑定。

CodeBuddy 一次实现后生成三个本地提交，四条批准的离线验收命令全部退出 0。CodeBuddy
CLI 没有提供 token usage；宿主日志落盘 helper 缺失导致完整 stdout 未保存，因此 CodeBuddy
token 与精确耗时均记为**不可得**，不作估算。

独立 Codex 审核实测：

| 指标 | 实测值 |
| --- | ---: |
| input tokens | 195,002 |
| cached input tokens | 154,880 |
| uncached input tokens | 40,122 |
| output tokens | 4,318 |
| reasoning output tokens（output 子集，不重复计入总量） | 2,145 |
| input + output | 199,320 |

审核结论为 `REVIEW_FAILED`，发现两个 P1：

1. 新的 worktree、changed-files、approved-command 绑定参数仍是可选，正式 orchestrator
   调用点没有传入，生产链路可绕过新约束；
2. Windows 单反斜杠根路径（例如 `\Windows\System32\...`）未被安全路径函数拒绝。

结果：`failed_quality_gate`，不进入返修与复审。

## 只能成立的成本观察

以下是 `cost per attempt`，不是 `cost per success`：

- 按 Codex `input + output` 计，审核相对独立实现少 73.564%；
- 去掉 cached input 后再加 output，审核相对独立实现少 24.935%；
- 两个口径差异很大，说明不能只看总 input，也必须保留 cached input；
- 协作组没有通过质量门禁，所以 73.564% 不能作为协作节省结论。

## 决策

1. 冻结通用编排器功能扩建，不继续为单次实验补协议。
2. 当前双 Agent 模式不作为默认开发路径。
3. 保留现有脚本用于明确、机械、中型任务的可选委派，但每次必须经过独立质量门禁。
4. 在出现新的天然成对真实任务前，不重复本实验，不为获得正结果人为制造任务。
5. 小任务、架构、安全和跨模块高耦合任务继续由 Codex 直接完成。

## 原始证据

原始数据保存在被 `.gitignore` 排除的本地目录：

- `.agent-collab/runs/economics-solo-20260915/`
- `.agent-collab/runs/economics-collab-20260915/`

两个隔离 worktree 与失败现场保留，未 merge、未 push。
