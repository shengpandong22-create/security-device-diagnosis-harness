# Codex 与 CodeBuddy 协作编排器设计与学习手册

## 1. 为什么要打通两个 Agent

这个需求不是为了让两个聊天框互相说话，而是解决一个真实限制：用户不可能始终坐在
电脑前充当复制粘贴中转站，Codex 和 CodeBuddy 又各有用量、轮次与超时限制。项目需要
在用户睡觉、上班或打球时继续推进，同时又不能因为无人盯守而越界运行、消耗周额度、
访问真实设备或把半成品误判为完成。

因此角色被明确拆开：Codex 是设计师、监工和质量审核员；CodeBuddy 是隔离 worktree
中的实现者；Git commit 是事实总线；JSON 状态文件是交接协议；进程退出事件是通知机制。
用户只负责定义方向和处理真正需要授权的决策。

## 2. 从轮询到事件与状态机

早期方案依赖轮询输出，容易把沉默、429、502、Max Turns 或退出码 0 的假成功混为一谈。
当前编排器等待操作系统 `Process.Exited` 事件，再分别校验：

1. 进程是否正常退出；
2. 输出是否命中限流、认证、502、Max Turns 等假成功；
3. 分支 HEAD 是否相对 base commit 前进；
4. worktree 是否干净；
5. `HANDOFF.json` 是否绑定相同 base/head，并包含验证证据；
6. 是否存在剩余项或越权外部动作。

如果调用方提供可信的宿主验收命令，编排器还会在 CodeBuddy 退出后自行执行，并生成
`HOST_VALIDATION.json`。只有上述门禁和宿主验收全部通过，状态才能从 `implementing`
进入 `ready_for_review`。

## 3. Protocol v2

```text
需求 + 历史薄弱点
  -> CodeBuddy 有界实现
  -> HANDOFF.json + commit
  -> Codex 严格审查指定 diff
  -> REVIEW_RESULT.json
  -> 最多一次返修
  -> approved / codex_takeover_required
```

关键文件：

| 文件 | 作用 |
|---|---|
| `STATE.json` | 当前有限状态、base/head 与失败原因 |
| `HANDOFF.json` | 实现文件、验证命令、剩余项、风险 |
| `NEEDS_CONTINUATION.json` | Max Turns 后的同现场续作凭证 |
| `REVIEW_RESULT.json` | Codex 对精确 commit 的裁决 |
| `HOST_VALIDATION.json` | 宿主执行可信命令后生成的 commit、文件 hash 与退出码证明 |
| `CODEX_USAGE.jsonl` | Codex 每次计划、审核和复审的精确 session 与 token usage |
| `CODEX_TAKEOVER.json` | 自动化预算耗尽后的人工/Codex 接管入口 |

`CODEX_TAKEOVER.json` 的意义不是“失败”，而是把失败变成可恢复状态：保留 worktree、
分支、HEAD、日志和未完成原因，Codex 从最后一个有效 commit 继续，不重新消耗 CodeBuddy。

## 4. 从 Phase 9 得到的薄弱点模型

编排提示词现在主动预防以下常见问题：

- 模型声称成功但没有 commit；
- commit 存在但工作区仍有未提交文件；
- Max Turns 后从头重跑，重复消耗额度；
- 502 或限流退出码为 0，被误当成功；
- 只跑新增测试，成功路径却漏掉 `return`；
- 异常处理直接拼接 `str(exc)`，把参数、URL 或凭证送给 LLM；
- 为通过测试修改标准答案或放松安全规则；
- 默认 Demo 隐式访问本机 BGE 或真实外部服务。

所以审查不只看新增测试，还必须检查历史契约、成功路径、安全反例、默认入口和实际 diff。

## 5. 预算与安全设计

- 任务规模 `probe/small/medium` 映射 8/24/36 turns；
- Max Turns 最多在同一 worktree 续作一次；
- Codex 审查不通过最多返修一次；
- 之后必须进入 `codex_takeover_required`，禁止无限循环；
- 两次调用共享时间边界，不使用任何用量重置次数；
- 默认 `external_access=forbidden`：禁止真实模型、BGE、设备、网络、push 和通知；
- 自动化永不 merge 或 push，批准只表示“可供主 Agent 合入”。

这是一种 Harness 思路：模型可以推理和执行，但状态转换、预算、权限和成功判定由确定性
程序掌握。

## 6. 如何使用

需求文件至少包含任务规模、允许修改、禁止事项、验证命令和历史风险：

```text
task_size: small
external_access: forbidden
```

运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_task.ps1 `
  -ReqFile .agent-collab/inbox/my-task.md `
  -HostValidationCommands @("uv run ruff check .", "uv run pytest tests/path -q")
```

仅验证协议，不调用任何模型：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_protocol_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_attestation_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/collab_codex_usage_probe.ps1
```

`HostValidationCommands` 必须来自用户/Codex 事先审定的需求，而不能直接执行 CodeBuddy
在 `HANDOFF.json` 中自由生成的命令。脚本只把退出码与输出 hash 写入证明，不把可能包含
环境信息的完整输出交给审核模型。Codex 在新只读会话中复核 diff、测试代码、base/head、
变更文件和 SHA-256，不重新执行宿主命令。最终裁决必须把 `REVIEW_PASSED` 或
`REVIEW_FAILED` 单独放在一行；进程退出码 0 本身不代表审核通过。

Codex CLI 使用 JSONL 模式运行。编排器把原始事件保存在本地运行目录，只把最后一条
Agent 消息写入 `PLAN.md` 或 `REVIEW.md`；每个 `turn.completed.usage` 追加为
`CODEX_USAGE.jsonl` 的一行，字段包括 timestamp、task_id、trigger、purpose、session_id、
input、cached input、output 与 reasoning output。JSONL 缺少 thread、最终消息或 usage 时，
该次调用不能被当作完整成功。这使后续可以按真实任务计算 Codex 的 cost per success，
而不是再用运行时长猜额度消耗。

结束后先看 `STATE.json`。`approved` 才进入人工合并检查；
`codex_takeover_required` 则按文件中的 worktree 和 head commit 恢复现场。

## 7. 仍然不能自动化的事

真实设备授权、密钥使用、破坏性操作、需求方向选择和生产合并必须由用户决定。
编排器解决的是“可靠接力”，不是把业务责任交给模型，也不保证 Agent 判断永远正确。

## 8. 使用边界：可靠接力不等于经济

后续真实 A/B 实验表明，这套编排器可以完成隔离、终态通知、证据保存和独立审核，但尚未
证明能够降低成功任务的 Codex token。两个实验路径都在质量门禁发现 P1，因此只能形成
`cost per attempt`，不能形成 `cost per success`。

项目现已把默认策略修正为：小任务、架构、安全和高耦合改动由 Codex 直接完成；只有需求
稳定、确定性验收充分、实现工作显著大于审核工作的中型机械任务才允许委派给 CodeBuddy；
独立研究或专项审计才考虑并行多 Agent。

完整的外部资料、适用场景、实测数据、委派准入卡和止损规则见：
[双 Agent 适用边界与 Codex / CodeBuddy 实验复盘](../experiments/codex-codebuddy-usage/双Agent适用边界与Codex-CodeBuddy实验复盘.md)。
