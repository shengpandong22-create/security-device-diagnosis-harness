# Phase 3A-1 Access Domain 试跑审查结论

> 试跑时间：2026-09-10  
> 隔离 worktree：`D:\AgentStudy\security-device-diagnosis-harness-phase3a-1-access-domain-20260910-015733`  
> 分支：`codex/phase3a-1-access-domain-20260910-015733`  
> 结论：协作链路部分通过；代码暂不建议合入 main。

## 1. 协作链路结论

本次验证证明：

- `orchestrator_task.ps1` 能创建隔离 worktree；
- Codex CLI 计划阶段超时时，脚本能使用需求文件 fallback；
- CodeBuddy 能在隔离 worktree 中修改代码；
- CodeBuddy 未直接修改 main；
- Phase 0/1/2 回归命令在该 worktree 中仍可通过。

但也暴露两个编排层问题：

1. CodeBuddy 输出 `Max turns exceeded` 时退出码仍可能是 0，不能只信退出码；
2. CodeBuddy 长时间静默，正式无人值守需要更强的失败模式识别和日志落盘。

已修复第 1 点：三个脚本均将 `Max turns exceeded` 识别为假成功。

## 2. 当前 CodeBuddy 产物

CodeBuddy 在隔离 worktree 中产生了：

- `src/security_diagnosis_harness/domain/access.py`
- `tests/domain/test_access.py`
- 修改 `src/security_diagnosis_harness/domain/__init__.py`

验证结果：

```text
uv run ruff check .                         -> passed
uv run pytest                               -> 453 passed, 2 warnings
uv run python scripts/demo_phase0_camera_black_screen.py -> passed
uv run python scripts/eval_phase1_camera_black_screen.py -> passed
uv run python scripts/eval_phase2_recording_missing.py -> passed
git diff --check                            -> passed
```

## 3. 暂不合入 main 的原因

### 3.1 与 Phase 3 验收文档命名不一致

当前 `docs/04-validation/Phase 3 验收标准.md` 中 Phase 3A 预期模型是：

- `AccessControllerSnapshot`
- `DoorSnapshot`
- `CredentialSnapshot`
- `AccessPolicySnapshot`
- `AccessEvent`

而 CodeBuddy 实现的是：

- `AccessPointSnapshot`
- `AccessEventSnapshot`
- `AccessPermissionCheck`
- `DoorStateSnapshot`

这套实现并非完全错误，但会导致后续 Phase 3B/3C 的 Tool、样例和规则命名被带偏。Phase 3 是一个要继续演进的业务域，模型命名必须先收敛。

### 3.2 缺少独立 CredentialSnapshot

门禁刷卡异常里，“凭证是否冻结/过期/类型”是核心事实。如果只把 `credential_type` 和失败原因放在事件或权限校验里，后续很难区分：

- 卡本身被冻结；
- 人没有门权限；
- 当前时间不在授权时段；
- 控制器离线导致无法校验。

因此 Phase 3A 应保留独立 `CredentialSnapshot`。

### 3.3 缺少授权时间窗口模型

Phase 3 计划覆盖 `time_window_denied`。当前实现没有可复用的授权时间窗口模型，不利于后续规则判断“有权限但不在授权时段”。

### 3.4 未同步 README 和 Phase 3 验收状态

本次任务要求可按需修改 README 与验收文档，但 CodeBuddy 没有更新。作为真实开发闭环，这不是致命问题，但不能作为完成态合入。

## 4. 返修目标

请把当前实现调整为与 Phase 3 验收文档一致的命名和边界。

必须做到：

1. 新增或调整为以下模型：
   - `AccessControllerSnapshot`
   - `DoorSnapshot`
   - `CredentialSnapshot`
   - `AccessPolicySnapshot`
   - `AccessEvent`
2. 保留必要枚举，但命名需服务上述模型；
3. 支持跨天授权时间窗口；
4. 卡号、人脸、指纹、PIN、Token、secret、手机号、身份证等敏感内容不得原样保存；
5. `AccessEvent` 能表达 `granted` / `denied` / `timeout`；
6. `denied` 必须带拒绝原因；
7. `granted` 不得带拒绝原因；
8. 模型不得包含 `confidence` / `root_cause` / `conclusion` / `final_status` 等结论字段；
9. `domain/access.py` 不得依赖 API、Tool、Runner、Adapter、数据库或真实模型 SDK；
10. README 和 `docs/04-validation/Phase 3 验收标准.md` 需要标记 Phase 3A-1 完成状态，但不得勾选 Phase 3B/3C。

## 5. 给 CodeBuddy 的返修提示词

```text
你正在隔离 worktree 中继续 Phase 3A-1 门禁刷卡异常领域模型任务。

Codex 审查结论：当前 access.py 的代码能通过测试，但模型命名和 Phase 3 验收文档不一致，暂不允许合入 main。

请按以下要求返修：

1. 将领域模型收敛到 docs/04-validation/Phase 3 验收标准.md 中的命名：
   - AccessControllerSnapshot
   - DoorSnapshot
   - CredentialSnapshot
   - AccessPolicySnapshot
   - AccessEvent
2. 保留必要枚举，但不要保留与上述模型冲突的旧命名，除非你能说明兼容原因。
3. 新增可表达跨天授权的时间窗口模型或等价结构，用于 AccessPolicySnapshot。
4. 完善 tests/domain/test_access.py，使测试覆盖：
   - 控制器离线；
   - 门锁异常；
   - 凭证被冻结或过期；
   - 没有门权限；
   - 不在授权时段；
   - 刷卡被拒；
   - 控制器超时；
   - granted 不能带拒绝原因；
   - denied 必须带拒绝原因；
   - 敏感字段脱敏；
   - 事实模型不包含结论字段；
   - domain/access.py 不依赖 API/Tool/Runner/Adapter/DB/LLM。
5. 更新 README 和 docs/04-validation/Phase 3 验收标准.md，只标记 Phase 3A-1/3A 领域模型部分完成，不要勾选 Phase 3B/3C。
6. 必须运行：
   uv run ruff check .
   uv run pytest
   uv run python scripts/demo_phase0_camera_black_screen.py
   uv run python scripts/eval_phase1_camera_black_screen.py
   uv run python scripts/eval_phase2_recording_missing.py
   git diff --check
7. 使用英文 conventional commit，本地提交即可，不要 push。

严禁：新增 API、Tool、Runner、DeviceGateway、样例 JSON、eval 脚本、数据库、RAG、真实模型、真实设备、前端。

完成后输出：修改文件列表、测试结果、git log --oneline -8、git status --short、偏离说明。
```
