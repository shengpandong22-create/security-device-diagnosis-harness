# Phase 10A：Simulator E2E History 与 Gate

task_size: medium
external_access: forbidden

## 目标

把 Phase 9D `simulator_e2e` 聚合结果接入独立、强类型、可比较的本地 History 与 Gate。
不要复用或篡改 Phase 7 real_model 的 SuiteMetrics 语义，也不要存储原始案例与设备标识。

## 允许修改

- 新建 `src/security_diagnosis_harness/evaluation/shadow_history.py`
- 最小修改 `scripts/eval_phase9d_simulator_shadow.py`，暴露稳定强类型输入
- 新建 `scripts/eval_phase10_shadow_gate.py`
- 新建 `tests/evaluation/test_shadow_history.py`
- 新建 `tests/test_eval_phase10_shadow_gate.py`
- 最小更新 Phase 10 验收文档

## 契约

1. 运行身份至少包含 code commit、suite name/version、report_kind=`simulator_e2e`、
   adapter_kind=`simulator`、配置 hash、场景集合 hash；不得包含设备 ID、endpoint、凭证。
2. History 只保存聚合摘要，原子写入，拒绝重复 run_id 和损坏文件。
3. 除 code commit/run_id/time 外的控制变量必须完全一致才可比较。
4. Gate 至少比较 completion rate、controlled degradation coverage、Evidence violation、P0、
   device writes、external notifications；P0/泄漏/写设备/通知/失败 Evidence 一律阻塞。
5. 不自动修改 Baseline，不混入 real_model 或 authorized_device_e2e。
6. 使用临时目录完成 baseline→candidate→gate 固定脚本；不得向仓库写历史数据。

## 历史缺陷预警

- 不要信任外部 summary 中自报的 P0，要从场景/指标重新计算可验证项；
- 不要用字符串包含关系判断 report_kind；
- 不要保存原始 `scenarios` 中可能出现的标识；
- 不要用设备 ID、run_id、自由文本做指标标签；
- 不要以 `gate_allowed=true` 代替逐项 Gate 计算；
- 成功路径必须显式 return，异常不得拼接 `str(exc)`。

## 验证

```powershell
uv run ruff check src/security_diagnosis_harness/evaluation/shadow_history.py scripts/eval_phase9d_simulator_shadow.py scripts/eval_phase10_shadow_gate.py tests/evaluation/test_shadow_history.py tests/test_eval_phase10_shadow_gate.py
uv run pytest tests/evaluation/test_shadow_history.py tests/test_eval_phase10_shadow_gate.py tests/evaluation/test_phase9_observability.py -q
uv run python scripts/eval_phase10_shadow_gate.py
git diff --check
```

提交：`feat(eval): add simulator shadow history gate`

禁止 push、merge、真实模型、BGE、真实设备、HTTP 出站、`.env`、密钥和未来 Phase 10B/10C。
