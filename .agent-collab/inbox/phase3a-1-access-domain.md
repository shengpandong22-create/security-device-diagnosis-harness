# Phase 3A-1：门禁刷卡异常领域事实模型

> 执行角色：CodeBuddy  
> 审核角色：Codex  
> 任务性质：Phase 3 的最小真实需求，不是完整 Phase 3A。  

## 1. 背景

当前工程已经完成：

- Phase 0：安防设备诊断 Harness 最小闭环；
- Phase 1：摄像头黑屏诊断深化；
- Phase 2：录像缺失诊断深化。

下一阶段 Phase 3 计划进入“门禁刷卡异常”场景。但本任务只做 Phase 3A 的第一小步：定义门禁相关领域事实模型，先让系统能表达门禁刷卡失败、门禁状态和权限校验事实。

## 2. 本次目标

新增门禁刷卡异常相关 Domain 模型，让后续 Tool、规则、报告和评测可以复用。

建议新增文件：

- `src/security_diagnosis_harness/domain/access.py`
- `tests/domain/test_access.py`

可按需修改：

- `src/security_diagnosis_harness/domain/__init__.py`
- `README.md`
- `docs/04-validation/Phase 3 验收标准.md`

## 3. 必须表达的领域事实

至少支持以下事实：

1. 门禁点/门禁设备基础快照；
2. 刷卡事件快照；
3. 权限校验结果；
4. 门状态/锁状态；
5. 常见刷卡失败原因。

建议枚举，但可以在不偏离语义的前提下微调命名：

- `AccessPointStatus`：`online` / `offline` / `degraded` / `unknown`
- `DoorLockStatus`：`locked` / `unlocked` / `jammed` / `unknown`
- `DoorOpenStatus`：`closed` / `open` / `forced_open` / `held_open` / `unknown`
- `CredentialType`：`card` / `face` / `qr_code` / `fingerprint` / `unknown`
- `AccessDecision`：`granted` / `denied` / `timeout` / `unknown`
- `AccessFailureReason`：`permission_denied` / `expired_credential` / `device_offline` / `controller_timeout` / `door_lock_error` / `unknown`

建议模型：

- `AccessPointSnapshot`
- `AccessEventSnapshot`
- `AccessPermissionCheck`
- `DoorStateSnapshot`

## 4. 领域边界要求

必须遵守：

- Domain 层只能依赖标准库、`pydantic` 和本领域已有基础模块；
- 不引入 API、Tool、Runner、Adapter、数据库、真实设备、真实模型；
- 不产生 confirmed，所有事实都只是 Evidence 候选材料；
- 不把“诊断结论”写进事实模型；
- 真实卡号、人脸、指纹、人员姓名、身份证、手机号等不得原样保存。

## 5. 脱敏要求

如果模型中出现以下字段或 extra 内容，必须脱敏：

- card number / card_no / card_id；
- face id / person id；
- phone；
- id card；
- token / password / secret / credential。

脱敏占位符沿用项目已有风格：

```text
***REDACTED***
```

## 6. 测试要求

至少新增领域测试，覆盖：

1. 能表达一次刷卡被拒；
2. 能表达门禁设备离线；
3. 能表达控制器超时；
4. 能表达门锁异常；
5. `granted` 与失败原因不能自相矛盾；
6. `denied` 建议必须带失败原因；
7. 真实卡号/人员标识/手机号/身份证/Token 不得原样保留；
8. 枚举值与文档一致；
9. 模型不能包含 conclusion/root_cause/final_status 等结论字段；
10. `domain/access.py` 不得依赖 infrastructure/API/tool/runner。

## 7. 验收命令

必须运行：

```powershell
uv run ruff check .
uv run pytest
uv run python scripts/demo_phase0_camera_black_screen.py
uv run python scripts/eval_phase1_camera_black_screen.py
uv run python scripts/eval_phase2_recording_missing.py
git diff --check
git status --short
```

## 8. 提交要求

请使用英文 conventional commit。

建议提交：

```text
feat(domain): add access diagnosis fact models
test(domain): cover access diagnosis facts
docs: mark phase 3a domain baseline
```

如果测试或文档较少，也可以合并为 1～2 个 commit，但必须说明。

## 9. 明确禁止

本任务不要实现：

- `access__query_event` 等工具；
- DeviceGateway 门禁方法；
- API；
- ToolLoopRunner 改造；
- CitationPolicy 改造；
- 规则推断；
- eval 脚本；
- 数据库；
- RAG；
- 真实设备；
- 真实模型；
- 前端。

如果发现必须越界才能完成，请停止并在报告中说明原因。
