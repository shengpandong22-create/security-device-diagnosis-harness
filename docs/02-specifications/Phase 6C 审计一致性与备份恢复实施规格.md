# Phase 6C 审计、一致性与备份恢复实施规格

## 1. 目标

Phase 6C 不增加诊断类型，而是为已经落地的 SQLite 运行时补齐三项数据可靠性能力：

1. 关键业务动作可审计；
2. 聚合损坏和引用异常可发现；
3. 数据库可以安全备份、校验并显式恢复。

本阶段不承诺分布式事务、高可用或灾备集群，只建立单机版本可验证的可靠性基线。

## 2. 分段实施

### Phase 6C-1：追加式审计事件

- 新增 `AuditEvent` 领域模型与 `AuditRepository` Port；
- SQLite 使用独立 `audit_events` 表，只允许 append/get/list，不提供 update/delete；
- 记录诊断创建、运行结果、人工审核和知识状态变更；
- 只记录 ID、动作、状态变化、聚合版本、执行结果和安全摘要；
- 不记录 Evidence payload、模型完整提示词、凭证或原始设备数据；
- 业务写成功而审计写失败时必须显式暴露，不得报告完整成功。

### Phase 6C-2：只读一致性扫描

- 检查状态与结论/审核记录是否自洽；
- 检查引用的 Evidence ID 是否存在且属于当前诊断；
- 检查 confirmed 是否存在人工 confirm 记录；
- 检查聚合 version 是否为正整数；
- 检查知识来源诊断、结论和 Evidence 引用是否可追踪；
- 输出结构化 finding 和 Markdown 报告；
- 扫描器只读，不自动修改数据库。

### Phase 6C-3：备份与恢复

- 仅支持文件型 SQLite；
- 使用 SQLite backup API 生成一致性快照，不直接复制正在使用的数据库文件；
- 生成包含 schema revision、文件大小、SHA-256、创建时间的 manifest；
- 恢复前校验 manifest、checksum、SQLite integrity 和 Alembic revision；
- Runtime 未关闭时拒绝恢复；
- 恢复前自动保存目标数据库的 recovery copy；
- 恢复失败不得破坏原数据库；
- `:memory:`、非 SQLite URL 和路径越界均受控拒绝。

## 3. 关键边界

- 审计事件是事实记录，不是业务聚合的第二份真相；
- 一致性扫描发现问题但不替用户决策修复；
- 备份成功必须以重新打开快照并通过 integrity check 为准；
- 恢复是显式运维动作，不暴露为普通诊断 API；
- 所有文本继续经过统一脱敏入口；
- 不自动重试 Agent、工具调用、人工审核或 CAS 冲突。

## 4. 完成定义

- Alembic 迁移可执行 `0002 -> head -> 0002 -> head` 且历史数据保留；
- 审计表只能追加，关键动作与聚合版本可关联；
- 注入损坏数据后扫描器能准确报告，正常数据库零阻塞级 finding；
- 真实文件型 SQLite 完成“写入 → 备份 → 继续修改 → 关闭 → 恢复 → 重启读取”；
- 篡改备份、错误 revision 和运行中恢复均被拒绝；
- 全量测试、Phase 0～5 回归、乐观锁与重启探针不回退。

