# Phase 8B 开发总结

## 1. 阶段目标

Phase 8B 把 Phase 7 Code Grader 的离散 Finding 转换为可运营的失败归因记录，回答四个问题：失败发生在哪一阶段、由哪个责任域跟进、最早在哪个版本出现、下一步应验证什么。

## 2. 已完成能力

- 建立 15 个现有 Finding code 的受治理目录；
- 统一为八类稳定阶段：dataset、perception、tool、evidence、conclusion、model、budget、infrastructure；
- 为每项配置责任域与静态安全排查建议；
- 以 `(case_id, finding_code)` 跨报告保留首次出现 commit 和数据集版本；
- 汇总受影响案例、阶段计数、责任域计数与 P0 数量；
- 输出原子写入的 JSON/Markdown 固定报告。

## 3. 关键边界

严重度只继承确定性 Code Grader，归因函数没有 override 参数，模型评分不能把 P0 降级。未知 Finding code 不做模糊推断，而是受控失败并要求先治理目录。报告不复制输入事实或 Finding 自由文本，建议采用“检查、核验、区分”等措辞，不把推测写成根因。

## 4. 固定验证

```powershell
uv run ruff check .
uv run pytest
uv run python scripts/eval_phase8_failure_attribution.py
```

固定脚本只验证失败归因协议，不调用真实模型、BGE 或设备，不代表生产准确率或生产故障率。

## 5. 下一阶段

Phase 8C 将在不保存原始事实、密钥或设备凭证的前提下持久化运行摘要，限定只有单变量可比运行进入趋势，并让确定性退化继续复用 Phase 7 发布门禁。
