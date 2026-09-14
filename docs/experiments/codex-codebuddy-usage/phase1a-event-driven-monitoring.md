# 阶段 1A：事件驱动协作监控

## 单一改造变量

本阶段只移除 Codex 主动轮询。独立 Review 会话在改造前已经由每次新建
`codex exec` 实现，本阶段不把它作为新增变量，也不声称测得了它的收益。

## 修改

- `AGENTS.md`：禁止 Codex 用模型调用查询运行中状态；只有终态事件可继续；
- `scripts/collab_process.ps1`：本地监控 Process.Exited、退出码、超时、目录/文件活动、
  心跳与稳定错误模式，并追加 `EVENTS.jsonl`；
- `scripts/orchestrator_task.ps1`：CodeBuddy实现和唯一续作接入事件日志、心跳和空闲超时；
- `scripts/orchestrator_monitor_probe.ps1`：用本地假进程覆盖六类状态，不调用模型。

## 状态机

```text
created -> running -> completed | failed | max_turns_exceeded | stalled | timeout
```

`created -> running` 写入事件但 `codex_wakeup=false`；每条路径只允许一个终态事件，
其 `codex_wakeup=true`。正常日志增长和心跳写入不会唤醒 Codex。

## 验证结果

六个离线案例全部达到预期，每例 running wakeup 为 0、terminal wakeup 为 1。
协议探针、Ruff 和全量 1610 项 pytest 通过。原始结果见
`raw/phase1a-validation.json`。

## 尚未验证

- 尚未运行阶段 2 的真实固定任务，因此 token 改造前后差值不可得；
- 尚未建立 session token 事件与协作 task_id 的稳定关联；
- heartbeat 表示本地编排器仍在观察进程，不代表模型服务内部一定有进展；
- 空闲超时基于 worktree 活动，阈值仍需阶段 2 观察误杀率；
- 没有证明 H1 的占比，也没有证明额度总消耗已经下降。

## 阶段 2A 建议方案（未执行）

选一个固定小任务，以相同 base commit、模型、turn、超时和验收命令分别运行旧版与
事件驱动版。每次新建 Codex 调用时记录 session 文件、purpose、trigger，并从对应
`token_count.last_token_usage` 原样采集 token。执行前需要用户确认。
