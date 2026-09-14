# AI 协作开发 Harness

本目录用于 Codex 与 CodeBuddy 通过文件、Git commit 和进程退出事件协作。

## 角色分工

- Codex：设计师、监工、质量审核员，负责规格、任务拆分、验收标准、代码审查和修复提示词。
- CodeBuddy：执行开发者，按 `inbox/` 中的任务实施代码和测试，并把结果写入 `outbox/`。

## 目录约定

```text
.agent-collab/
  inbox/      Codex 写给 CodeBuddy 的任务
  outbox/     CodeBuddy 写回的执行报告
  review/     Codex 的审核结论与返修清单
  rules/      长期协作规则和模板
  runs/       每次运行的状态、交接、审查、接管和日志证据
```

## 硬约束

- 每轮只做一个最小闭环任务。
- 不允许越界开发下一阶段。
- 不允许提交 `.env`、API Key、Token、真实设备凭证、生物特征数据。
- 未经任务明确允许，不接真实模型、真实设备、数据库、RAG、前端或写操作工具。
- 每轮必须报告修改文件、测试结果、偏离说明和 `git status`。

## 任务规模与预算

需求文件应在标题后声明一行任务规模：

```text
task_size: small
```

可选值及单次 CodeBuddy 轮次预算：

| task_size | 适用任务 | max turns |
|---|---|---:|
| `probe` | 登录、进程退出、单文件写入等链路探针 | 8 |
| `small` | 单一领域对象、Adapter 或局部修复闭环 | 24 |
| `medium` | 有明确边界的多文件功能闭环 | 36 |

命令行 `-TaskSize` 优先于需求文件；显式 `-CodeBuddyMaxTurns` 可覆盖映射值。
未声明任务规模时按 `small` 处理。

达到 `Max turns` 后，编排器不会触发 Codex 审查，而是在运行目录写入
`NEEDS_CONTINUATION.json`。默认仅允许 CodeBuddy 在同一 worktree 自动续作一次，
两次调用共享 `CodeBuddyTimeoutSeconds` 总时间预算。续作仍失败时立即停止，保留
worktree、分支、日志和状态文件供人工检查；编排器永不自动合并或推送。

## Protocol v2 状态机

```text
implementing -> needs_continuation -> ready_for_review
  -> changes_requested -> repairing -> ready_for_re_review -> approved
  -> codex_takeover_required（任一不可恢复失败）
```

- `STATE.json`：当前状态与精确 base/head commit；
- `HANDOFF.json`：CodeBuddy 的结构化交接，必须声明修改文件、验证、剩余项和风险；
- `REVIEW_RESULT.json`：Codex 对指定 commit 的机器可读裁决；
- `CODEX_TAKEOVER.json`：两次实现或一次返修仍失败时保留现场，禁止从头重跑；
- `NEEDS_CONTINUATION.json`：Max Turns 的有界续作证据。

实现成功但缺少合法 `HANDOFF.json`、HEAD 未推进、工作区不干净、存在未完成项或
验证为空时，均不得进入 Codex 审查。默认禁止真实模型、BGE、设备、网络、push 和通知。

Windows 下编排器优先使用官方 npm 入口 `%APPDATA%\npm\codebuddy.cmd`，避免未签名
原生 Beta 二进制被 WDAC 拦截；当前默认实现模型为 `deepseek-v4.1-flash`，仍可通过
`-CodeBuddyModel` 对单次任务覆盖。
