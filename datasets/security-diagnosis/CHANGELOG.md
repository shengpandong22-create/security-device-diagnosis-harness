# Security Diagnosis Dataset Changelog

## 1.0.0 - 2026-09-12

- 建立 Dev、Validation、Test 三套物理隔离的数据集。
- 引入案例来源谱系、工具白名单、证据要求、禁止行为和预算协议。
- 增加 `expected_tools`，区分允许调用与完成案例所需调用，用于工具 precision/recall。
- 当前仅用于验证数据治理机制，不宣称样本规模足以衡量生产准确率。
