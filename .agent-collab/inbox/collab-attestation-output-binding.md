# Collaboration Harness：绑定宿主验证命令与输出证据

task_size: small
external_access: forbidden

## 1. 背景

`HOST_VALIDATION.json` 已绑定 base/head、变更文件、文件 SHA-256 和验证退出码，但当前
验证器没有接收“预先批准的命令集合”，也没有根据落盘的宿主输出重新计算
`output_sha256`。这意味着 attestation 文件被修改后，命令或输出 hash 的声明可能无法被
本地确定性门禁发现。

## 2. 目标

让宿主验证输出形成可重新计算、可限制路径、可绑定批准命令的证明。不要修改业务源码。

## 3. 允许修改

- `scripts/collab_attestation.ps1`
- `scripts/orchestrator_attestation_probe.ps1`
- `scripts/orchestrator_task.ps1`
- `.agent-collab/README.md`
- `docs/00-overview/Codex与CodeBuddy协作编排器设计与学习手册.md`

除非为了最小测试夹具，不得修改其它文件。

## 4. 必须实现

1. 每条宿主验证命令的完整输出写入 attestation 所在目录下的独立日志文件；文件名由宿主
   脚本按稳定序号产生，不能来自命令文本。
2. attestation 的每个 validation 条目记录命令、退出码、超时、输出文件名和 SHA-256。
3. `Test-HostValidationAttestation` 必须接收预先批准的 `ExpectedCommands`，并按数量、
   顺序、逐字内容核对，不能信任 JSON 自己声明的命令。
4. 验证器必须读取落盘输出并重新计算 SHA-256；文件缺失、hash 篡改、命令篡改均拒绝。
5. 输出文件必须限制在 attestation 文件所在目录，拒绝绝对路径、`..`、目录分隔符和路径
   穿越。
6. `orchestrator_task.ps1` 必须把原始 `HostValidationCommands` 传给验证器。
7. 扩展离线探针，至少证明：合法证明接受；命令篡改、输出内容篡改、输出 hash 篡改、
   输出文件缺失、路径穿越全部拒绝。
8. 不降低现有 base/head、工作区整洁、变更文件和源码 hash 校验。

## 5. 禁止事项

- 不调用 Codex、CodeBuddy、真实模型、BGE、设备或网络作为测试的一部分。
- 不执行 `HANDOFF.json` 中由模型自由生成的命令。
- 不把宿主输出正文嵌入发给审核模型的 prompt；Codex 只获得 attestation 路径并复算 hash。
- 不读取 `.env`、密钥、Token、密码、真实 IP 或设备凭证。
- 不 push、merge、删除 worktree 或改写历史。
- 不修改业务 `src/`、领域模型、API、数据库或评测标准答案。

## 6. 宿主验收命令

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_attestation_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/collab_codex_usage_probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/orchestrator_protocol_probe.ps1
```

三条必须全部退出 0。不得以 stderr 是否为空代替退出码。

## 7. 交付要求

- 使用英文 conventional commit；
- 工作区最终干净；
- `HANDOFF.json` 必须符合 Protocol v2；
- 报告修改文件、验证退出码、负面用例、偏离和未实现边界。
