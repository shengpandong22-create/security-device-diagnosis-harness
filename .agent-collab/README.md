# AI 协作开发 Harness

本目录用于 Codex 与 CodeBuddy 通过文件协议协作。

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
```

## 硬约束

- 每轮只做一个最小闭环任务。
- 不允许越界开发下一阶段。
- 不允许提交 `.env`、API Key、Token、真实设备凭证、生物特征数据。
- 未经任务明确允许，不接真实模型、真实设备、数据库、RAG、前端或写操作工具。
- 每轮必须报告修改文件、测试结果、偏离说明和 `git status`。

