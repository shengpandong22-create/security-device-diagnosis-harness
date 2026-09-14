# Phase 2C 首个真实任务运行报告

## 结论

本次真实任务未成功，流程在 CodeBuddy 实现门禁停止，没有调用 Codex 审核，因此没有
形成 `cost per success`。隔离 worktree、分支、日志和未提交 diff 均按协议保留，没有
自动续作、返修、合并或 push。

## 固定配置与实测结果

| 项目 | 实测值 |
| --- | --- |
| task | `collab-attestation-output-binding` |
| task size | `small` |
| CodeBuddy model | `deepseek-v4.1-flash` |
| CodeBuddy max turns | 24 |
| CodeBuddy attempts | 1 |
| CodeBuddy elapsed | 162.261 秒 |
| completion signal | `max_turns_exceeded` |
| CodeBuddy process exit code | 0 |
| implementation commit | 无 |
| HANDOFF.json | 无 |
| worktree clean | 否 |
| Codex calls | 0 |
| Codex usage | 不存在 |
| final state | `codex_takeover_required` |

CodeBuddy CLI 没有提供 token usage，因此 CodeBuddy token 成本不可得。退出码虽然为 0，
本地事件检测仍识别出 `Max turns (24) exceeded`，证明假成功门禁生效。

## 已保留现场

- branch：`codex/collab-attestation-output-binding-20260915-002712`
- base/head：`4a33df1c8c8e7802270d5a9699c1e9f8fede9858`（HEAD 未前进）
- worktree：`D:\AgentStudy\security-device-diagnosis-harness-collab-attestation-output-binding-20260915-002712`
- run：`.agent-collab/runs/collab-attestation-output-binding-20260915-002712`
- 未提交修改：`collab_attestation.ps1`、`orchestrator_attestation_probe.ps1`、`orchestrator_task.ps1`

## 只读质量审计

未提交实现已经覆盖输出文件落盘、批准命令绑定和路径检查的大部分主体，但暂不能接收：

1. 任务实际产生约 179 行新增、45 行删除，并包含实现、编排接线和大量负面探针，`small`
   粒度判断过于乐观；
2. 探针连续修改同一个 attestation，部分负面场景开始前没有恢复 pristine 状态；后续拒绝
   可能由前一个残留篡改触发，无法证明当前被测变量真的受到保护；
3. CodeBuddy 没有运行并报告最终验收、没有提交、没有生成 Protocol v2 HANDOFF；
4. 因实现门禁失败，宿主三条验证与 Codex 审核均未执行，不能用未提交代码的局部完成度
   冒充成功。

## 对协作流程的正向结论

- 事件检测能识别“退出码 0 + Max turns”假成功；
- 失败实现不会消耗 Codex 审核额度；
- 无续作策略生效，没有从头重跑；
- worktree 和接管信息完整保留；
- `CODEX_USAGE.jsonl` 不存在准确表达本次 Codex 调用数为 0。

## 下一步

不应把 max turns 从 24 直接调高后原样重跑。应先把任务拆成两个独立闭环：

1. 2C-1：只实现输出日志落盘、批准命令绑定和验证器；用少量独立反例测试；
2. 2C-2：再扩展完整篡改矩阵、编排器接线和文档。

每个负面用例必须从 pristine fixture 独立开始。是否启动新的真实模型任务，需要重新确认；
当前现场不自动续作。
