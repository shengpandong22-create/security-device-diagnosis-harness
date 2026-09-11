# Phase 6C 验收标准

## 1. 6C-1 追加式审计

- [x] AuditEvent 不包含设备事实正文和敏感凭证
- [x] SQLite 审计仓储只提供 append/get/list
- [x] 诊断创建、运行、人工审核和知识治理动作可追踪
- [x] 事件包含聚合 ID、动作、结果、前后状态和版本
- [x] 审计写入失败不会伪装成完整成功
- [x] 迁移可逆且历史数据保留

## 2. 6C-2 一致性扫描

- [x] 正常数据无阻塞级 finding
- [x] 能发现跨诊断引用、未知 Evidence、无人工确认的 confirmed
- [x] 能发现非法版本和知识来源断链
- [x] 扫描过程不修改任何聚合
- [x] 输出 JSON 与 Markdown 报告

## 3. 6C-3 备份恢复

- [x] 使用 SQLite backup API 生成一致快照
- [x] manifest 包含 revision、大小、SHA-256 与时间
- [x] checksum、integrity、revision 校验通过后才允许恢复
- [x] Runtime 运行中恢复被拒绝
- [x] 恢复失败不破坏当前数据库
- [x] 恢复后诊断、Evidence、结论、审核、知识和审计均可读取

## 4. 总回归

- [x] `uv run ruff check .`
- [x] `uv run pytest`
- [x] Phase 0～5 固定回归通过
- [x] 乐观锁、重启恢复和跨域探针通过
- [x] 无数据库临时文件和敏感信息进入 Git

## 5. 完成状态

Phase 6C-1、6C-2、6C-3 已完成。当前能力是单机 SQLite 可靠性基线，
不是分布式审计、跨节点灾备或自动数据修复。审计与聚合写入尚未合并为同一
Unit of Work；审计失败会显式上抛，但已成功的业务写入不会被伪装回滚。
