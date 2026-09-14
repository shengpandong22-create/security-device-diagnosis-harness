# Economics A：Codex usage 原始日志完整性绑定

## 目标

让 `CODEX_USAGE.jsonl` 中的每条计量记录能够证明自己对应哪一个不可变的 Codex
原始 JSONL 日志，防止日志缺失、替换或调用元数据错配后仍被当作有效成本数据。

## 允许修改

- `scripts/collab_codex_usage.ps1`
- 新增 `scripts/collab_codex_usage_integrity_probe.ps1`
- 最小更新 `.agent-collab/README.md`

## 必须实现

1. 计量记录增加原始日志的仓库外相对文件名、SHA-256、字节数，以及固定的 model、
   purpose、trigger、task_id；不得记录绝对路径。
2. 提供只读验证函数，显式接收 usage 文件、原始日志目录和期望的 task/model/purpose/
   trigger，逐条验证：文件存在、文件名安全、hash/size 相符、Codex JSONL 可完整解析、
   session/token 字段与计量记录一致。
3. 拒绝：缺文件、内容篡改、hash 篡改、size 篡改、session/token 篡改、task/model/
   purpose/trigger 错配、绝对路径和 `..` 路径穿越。
4. 原有 `collab_codex_usage_probe.ps1` 继续通过；新 probe 每个负面场景从 pristine
   fixture 独立开始，最终输出结构化 JSON 并以退出码表达结果。
5. 兼容 Windows PowerShell 5.1；hash 使用 .NET API，不依赖 `Get-FileHash`。

## 禁止

- 不修改业务源码、编排状态机或 CodeBuddy协议。
- 不访问网络，不调用真实设备、LLM、BGE，不读取 `.env`。
- 不伪造测试输出，不放宽旧校验。

## 验收命令

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/collab_codex_usage_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/collab_codex_usage_integrity_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_protocol_probe.ps1
```

三条命令必须退出 0；工作树干净；提交一个或多个英文 conventional commits；禁止 push。
