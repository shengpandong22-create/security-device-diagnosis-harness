# Codex × CodeBuddy 协作规则

## 固定流程

```text
Codex 写任务规格
  -> CodeBuddy 按任务开发
  -> CodeBuddy 运行验收命令
  -> CodeBuddy 写 outbox 报告
  -> Codex 审核代码、测试、文档和边界
  -> 不通过则 Codex 写 review 返修单
  -> CodeBuddy 修复
```

## CodeBuddy 每轮报告格式

```markdown
# CodeBuddy 执行报告

## 1. 修改文件列表

## 2. 实现说明

## 3. 测试与验收结果

## 4. git log --oneline

## 5. git status

## 6. 偏离说明

## 7. 明确未实现内容
```

## 禁止事项

- 禁止提交真实密钥、Token、设备凭证、生物特征、真实卡号。
- 禁止绕过 ToolRegistry。
- 禁止让模型或规则直接产生 confirmed。
- 禁止默认调用真实模型或真实设备。
- 禁止未说明原因的大面积重构。

