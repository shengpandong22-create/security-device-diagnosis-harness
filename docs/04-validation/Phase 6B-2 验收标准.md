# Phase 6B-2 验收标准

> 验收对象：乐观锁与并发状态更新保护（DiagnosisCase / KnowledgeCandidate）。
> 本阶段**不是**"完全并发安全"，而是**乐观锁冲突检测（CAS）**：
> 陈旧副本不能覆盖较新的持久化状态。

## 1. 语义边界（重要）

本阶段实现的是 **optimistic concurrency control（冲突检测）**：

- 读取 `version=N` → 修改副本 → `UPDATE ... WHERE id=? AND version=N`；
- `rowcount == 1` → 提交，`version = N + 1`；
- `rowcount == 0` → 先 rollback，再检查 ID：
  - ID 不存在 → `DiagnosisNotFoundError` / `KnowledgeNotFoundError`；
  - ID 存在 → `ConcurrentUpdateError`。

**不**宣称：

- 数据库实时串行化；
- 分布式锁；
- 自动冲突合并；
- 自动重试后必然成功。

未实现：悲观锁、长事务、锁住 LLM / Tool 执行过程。

## 2. 领域版本字段

| 字段 | 默认值 | 语义 |
|---|---|---|
| `SecurityDiagnosisCase.version` | `0`（`ge=0`） | 只代表成功持久化次数 |
| `KnowledgeCandidate.version` | `0`（`ge=0`） | 同上 |

- 新建未保存：`version=0`；
- 首次 `save()` 成功：持久化 `version=1`；
- 每次 `update()` 成功：`version + 1`；
- 普通 Domain 状态变化（`_touch()` / 改字段）**不**自增 version；
- `version` 只由 Repository 写入，Domain 不自行递增。

禁止项均已满足：

- [x] API 创建请求不暴露 `version` 输入；
- [x] `_touch()` 不自动增加 version；
- [x] `save(version != 0)` 受控拒绝（`ValueError`），不静默改成 1；
- [x] `update(version <= 0)`（未保存聚合）受控拒绝；
- [x] 不使用 `updated_at` 或浮点时间做 CAS；
- [x] Repository 不就地修改调用方对象，返回值带最新 version。

## 3. Repository 行为

两个 Adapter 语义一致：

| 场景 | InMemory | SQLite |
|---|---|---|
| `save` 新聚合 | 落库 version=1 | 落库 version=1 |
| `save` 重复 ID | `DiagnosisAlreadyExistsError` | 同（CAS 前依赖主键约束） |
| `save(version != 0)` | `ValueError` | `ValueError` |
| `update` 版本一致 | version+1，返回新副本 | version+1，返回新副本 |
| `update` 版本陈旧 | `ConcurrentUpdateError` | `ConcurrentUpdateError` |
| `update` ID 不存在 | `*NotFoundError` | `*NotFoundError` |
| `update` CAS 后 commit 失败 | — | rollback + `RepositoryPersistenceError` |

- [x] InMemory 使用 `RLock` 保护「比较版本 + 写入」临界区；
- [x] SQLite 直接使用 CAS UPDATE，**不**先查 version 再无条件更新；
- [x] `rowcount == 0` 时先 rollback 再检查 ID；
- [x] CAS 成功后 `commit()` 失败 → rollback + `RepositoryPersistenceError`，
      不返回已递增版本的假成功对象；
- [x] 冲突请求不能覆盖成功请求；
- [x] 不自动重试 Agent / Tool / HumanReview。

## 4. Application 层

- [x] 三处 `repository.update(case)` 均改为 `case = self._repository.update(case)`：
      正常诊断完成、`_finish_failure()`、`review_diagnosis()`；
- [x] 未为 version 修改 Agent Runner、Evidence 或 CitationPolicy；
- [x] `supported_fault_types` 等 6B-1 能力约束不受影响。

## 5. API 映射

| 异常 | 状态码 | code | message |
|---|---|---|---|
| `ConcurrentUpdateError` | **409** | `concurrent_update` | `诊断已被其他请求更新，请刷新后重试` |

- [x] 不返回 500；
- [x] 响应体不暴露 expected / actual version；
- [x] 不暴露内部工具、类名或文件路径。

## 6. 迁移（0002）

`migrations/versions/0002_add_aggregate_versions.py`（`Revises: 0001`）：

- [x] `diagnosis_cases.version` / `knowledge_candidates.version`：
      `INTEGER NOT NULL DEFAULT 1`，使用 `op.batch_alter_table`；
- [x] 旧数据升级后 `version=1`；
- [x] 新聚合 Domain 初始 `version=0`，Repository 首次 save 写 1；
- [x] ORM 保留安全 server default，但业务逻辑显式写入版本，
      不依赖数据库默认值修正非法输入；
- [x] `downgrade` 只移除 version 列；
- [x] 未修改 `0001`；
- [x] `upgrade head` → `downgrade base` → `upgrade head` 全部可逆。

## 7. 真实并发探针

`scripts/demo_phase6_optimistic_lock.py`（文件型 SQLite + 两个独立 Repository）：

```text
STALE_UPDATE_REJECTED: True
WINNER_VERSION: 2
WINNER_DATA_PRESERVED: True
LOSER_DATA_ABSENT: True
AUTOMATIC_AGENT_RETRY: False
AUTOMATIC_REVIEW_RETRY: False
```

制造方式：A / B 各自 `get()` 得到**独立副本**（`copy_a is not copy_b`），
A 先 `update` 成功，B 再用陈旧副本 `update` → 被拒；
最终读取确认赢家数据与版本保留、输家数据不存在。

## 8. 验收命令与结果

```text
uv run ruff check .   -> All checks passed!
uv run pytest         -> 995 passed
git diff --check      -> 通过
uv run python scripts/demo_phase6_persistence_restart.py   -> restart_recovered=true
uv run python scripts/demo_phase6_optimistic_lock.py       -> 见第 7 节
uv run python scripts/probe_cross_fault_guard.py           -> 6B-1 护栏未回退
Phase 0 demo / Phase 1~4 eval / Phase 5 离线评测           -> 全部通过
```

## 9. 明确未实现

- 悲观锁、行锁、长事务；
- 锁住 LLM / Tool 执行过程；
- 自动冲突合并与自动重试；
- 分布式锁；
- 完整 readiness / 实时数据库探活；
- Knowledge 管理 API。
