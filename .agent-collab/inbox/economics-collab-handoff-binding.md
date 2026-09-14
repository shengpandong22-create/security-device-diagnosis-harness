# Economics B：CodeBuddy HANDOFF 与实际交付严格绑定

## 目标

强化 Protocol v2 HANDOFF，使 Codex只需验证一份有宿主事实约束的交付包，不必重新探索
整个工作区。HANDOFF 必须精确绑定实际 diff 和预先批准的验证命令。

## 允许修改

- `scripts/collab_protocol.ps1`
- 新增 `scripts/collab_handoff_integrity_probe.ps1`
- 最小更新 `.agent-collab/README.md`

## 必须实现

1. `Test-CodeBuddyHandoff` 新增显式 `WorktreeRoot`、`ExpectedChangedFiles` 和
   `ExpectedValidationCommands` 输入。
2. HANDOFF 的 `changed_files` 必须与期望集合及 `base...head` 的实际 diff 精确一致；
   文件名使用仓库相对路径，拒绝绝对路径与 `..` 穿越。
3. `validation` 的数量、顺序和 command 文本必须与批准命令精确一致；仍要求 exit_code=0，
   并保留既有离线命令限制。
4. 拒绝：文件遗漏/注入/顺序变化、实际 diff 错配、命令遗漏/注入/换序/文本篡改、
   非零退出码、路径穿越和绝对路径。
5. 新 probe 每个负面场景从 pristine fixture 独立开始；最终输出结构化 JSON并以退出码
   表达结果。既有 protocol、usage 和 attestation probes 不得回退。
6. 兼容 Windows PowerShell 5.1。

## 禁止

- 不修改业务源码、Codex usage 实现、Host Validation attestation 或编排状态机。
- 不访问网络，不调用真实设备、LLM、BGE，不读取 `.env`。
- 不伪造 HANDOFF、commit 或测试结果，不放宽旧校验。

## 验收命令

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_protocol_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/collab_handoff_integrity_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/collab_codex_usage_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_attestation_probe.ps1
```

四条命令必须退出 0；工作树干净；提交一个或多个英文 conventional commits；禁止 push。
