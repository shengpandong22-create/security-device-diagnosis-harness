# 无密钥 CI 与本地一键验证

## 目标

该门禁只验证仓库在干净环境中的工程可复现性：锁定依赖、静态检查、全量测试、独立
`adversarial_eval` 和确定性 `simulator_e2e`。它不启动 Device Lab，不访问真机，不调用真实模型，
也不写回任何版本化 baseline。

## CI 精确步骤

`.github/workflows/verify.yml` 使用 Python 3.12 和 uv，顺序固定为：

1. `uv sync --frozen`
2. `uv run ruff check .`
3. `uv run pytest`
4. `uv run python scripts/eval_adversarial_cases.py`，输出仅写入被忽略的 `demo-output/`
5. `uv run python scripts/eval_phase9a_simulator_e2e.py`
6. `git diff --check`

工作流把真实模型、真实设备、Device Lab 与正式 API 凭证相关环境变量显式设为空，不引用
GitHub Secret。Simulator 结果仍只表示本地确定性契约，不代表真机或生产准确率。

## 本地一键验证

```powershell
pwsh -NoProfile -File scripts/verify_release.ps1
```

脚本先打印 commit、Python/uv 版本和检测到的真实调用变量数量。只要任一真实模型、真机、
Device Lab 或 API 凭证变量非空，就以退出码 2 拒绝运行且不打印变量值。通过环境检查后执行与
CI 相同的核心步骤，并额外连续运行两次 adversarial suite，比较排除随机诊断/Evidence ID 和
运行时间后的语义结果。

所有临时报告写入 `demo-output/verify-release/`。脚本不部署服务、不启动 Docker、不修改规则、
不更新 fixed、simulator_e2e 或 real_model baseline。
