# Phase 2B 单次 Attestation 审核报告

## 结论

Phase 2B 成功。一个预先通过宿主门禁的 fixture 只调用一次全新 Codex 审核会话，最终明确返回 `APPROVED`。本阶段没有调用 CodeBuddy、没有轮询、没有重试，也没有执行额度重置。

## 固定边界

- Codex 模型：`gpt-5.6-luna`
- Codex 调用次数：1
- CodeBuddy 调用次数：0
- 宿主测试：10/10，退出码 0
- 审核输入：实现 diff、测试代码、attestation v1
- 审核动作：复算源码与测试 SHA-256，核对基线 commit、Git 变更范围和工作区状态
- 审核环境不执行 Python，只读取宿主测试证明

## 实测 Usage

| 指标 | 数值 |
| --- | ---: |
| input tokens | 100,868 |
| cached input tokens | 94,720 |
| output tokens | 2,221 |
| reasoning output tokens | 1,012 |
| Codex 调用次数 | 1 |
| 审核结果 | APPROVED |

本轮的 `cost per accepted review` 为一次调用、100,868 input tokens。它不是完整任务的 `cost per success`：实现是预先准备的，且没有计入 CodeBuddy 成本。

## 审核证据

Codex 确认：

- 实现满足 `strip()`、小写化、首字符 `[a-z]`、总长 1～64、尾部字符 `[a-z0-9._-]`；
- 两个 attested SHA-256 均匹配；
- attested base commit 与 `HEAD~1` 一致；
- 声明的业务变更范围与 Git 一致，attestation 是唯一额外提交产物；
- 工作区干净；
- 按约束没有在审核环境执行 Python。

最终消息以 `APPROVED` 开头，编排器据此返回成功。Codex CLI 退出码仍被记录，但不再单独作为审核成功依据。

## 对协作流程的意义

该实验验证了以下职责拆分可以工作：

1. CodeBuddy 负责实现，但不负责可信地声明成功；
2. 宿主脚本负责执行测试、限制变更范围、生成 hash 与 handoff commit；
3. Codex 在全新只读会话中负责语义和证据一致性审核；
4. 编排器只有收到明确 `APPROVED` 才进入成功终态。

这消除了 LLM 轮询、审核环境执行权限差异以及“进程成功等于审核成功”三类风险。它不证明 Codex 的每次语义判断都正确，也没有回答 Codex 自实现与 Codex 审核的成本比较。

## H1～H5 当前判定

| 假设 | 判定 | 当前证据 |
| --- | --- | --- |
| H1：轮询占历史总消耗超过 50% | 无法判定 | Phase 2A 最终 A 路径中轮询占 53.29%，但不能外推历史总体。 |
| H2：同会话轮询使后续输入膨胀 | 实验范围内成立 | 四次 A 运行均出现第一次约 31K、第二次约 62K 的单调增长。尚不能解释服务端内部计费因果。 |
| H3：审核成本约等于 Codex 自实现 | 无法判定 | Phase 2B 审核为 100,868 input tokens，但没有自实现对照。 |
| H4：额度是总量配额而非速率限制 | 无法判定 | 现有窗口百分比不能给出单次调用到账户额度的精确计费映射。 |
| H5：动态前缀使缓存失效 | 无法判定 | 本轮 cached input 为 94,720，但没有服务端 cache key 与计费原因。 |

## 后续边界

Phase 2B 已完成，不需要再重复 attestation 审核。若继续测量，应选择“Codex 审核”与“Codex 自实现”的单变量成本对照，且必须另行确认真实模型调用预算。
