# Codex / CodeBuddy 协作经济性 A/B 实验计划

## 决策问题

在质量不下降且任务真正完成时，`CodeBuddy 实现 + Codex 审核` 是否比 `Codex 独立实现`
减少至少 30% 的 Codex 总 token。

## 固定设计

- 同一基准 commit、两个独立 worktree，不交叉读取实现。
- 两个任务均为中型、离线、PowerShell 协作协议完整性改造，允许文件数和验收规模相近。
- Codex：`gpt-5.6-luna`、新会话；A 组一次实现，B 组一次审核；只有 P0/P1 才允许一次复审。
- CodeBuddy：`deepseek-v4.1-flash`、`medium` effort、max turns 36、最多一次实现，不续跑。
- 每次 Codex调用保存原始 JSONL，并记录 input/cached input/output/reasoning tokens。
- 不用耗尽时间推算 token；CodeBuddy token 不可得时明确记录不可得。

## 成功门槛

每组必须满足需求、全部批准验收命令退出 0、工作树干净、独立审核无 P0/P1。失败组不计算
节省率，只计入 `cost per attempt`。

## 决策门槛

- 协作组 Codex token 降低至少 30%，且质量不下降：保留中型任务委派。
- 降低 10%～30%：仅用于机械性实现。
- 降低不足 10%：无足够收益。
- Codex token 不降反升或协议连续失败：停止通用双 Agent编排。

本实验不把此前包含多次 CodeBuddy尝试和 Codex接管的 2C 现场作为成功样本。
