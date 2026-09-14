# Phase 10B-1：授权清单与调用前预算预检

task_size: small
external_access: forbidden

## 目标

实现独立、纯领域/应用级的授权清单与预算预检，不接入真实 HTTP Adapter。
任何未来设备调用必须能在调用前得到 allow/deny 结果；默认拒绝。

## 允许修改

- 新建 `src/security_diagnosis_harness/device_authorization.py`
- 最小导出公共类型
- 新建 `tests/test_device_authorization.py`
- 最小更新 `docs/04-validation/Phase 10 验收标准.md`

## 必须实现

1. 强类型 `AuthorizationManifest`：manifest_id、environment_alias、asset_scope_aliases、
   credential_ref、valid_from、valid_until、allowed_operations、max_total_calls、
   max_calls_per_operation；只允许只读操作。
2. `credential_ref` 仅允许不透明引用（例如 `vault-ref:camera-readonly`），禁止 URL、
   userinfo、IP、secret/token/password 字样与疑似明文凭证。
3. `AuthorizationRequest` 与不可变 `AuthorizationBudgetState`；预检函数不得修改输入。
4. 默认拒绝：无清单、未生效、已过期、资产越界、操作越界、总预算耗尽、单操作预算耗尽，
   都返回强类型 deny reason；不得拼接异常原文。
5. allow 结果返回消费后的新预算状态；deny 不消费预算。
6. 时间必须 aware；边界语义明确为 `valid_from <= now < valid_until`。
7. 禁止自动重试、自动扩容预算、自动通知、设备写操作。

## 历史缺陷预警

- 不要只验证操作名含 `query/read/check/search`；使用明确只读枚举或精确 allowlist；
- 不要让 `credential_ref` 成为秘密存储后门；
- 不要在 deny 之后仍递增计数；
- 不要用可变 dict 共享预算状态；
- 不要遗漏总预算与单操作预算同时成立时的原子判定；
- 不要把授权清单存在等同于授权有效；
- 不读取环境变量或 `.env`，不访问网络。

## 验证

```powershell
uv run ruff check src/security_diagnosis_harness/device_authorization.py tests/test_device_authorization.py
uv run pytest tests/test_device_authorization.py -q
git diff --check
```

提交：`feat(device): add authorization budget preflight`

禁止 push、真实设备、真实模型、BGE、HTTP、数据库迁移、Runtime 接线与 Phase 10C。
