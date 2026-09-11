# Phase 6C 验收标准

## 1. 6C-1 追加式审计

- [ ] AuditEvent 不包含设备事实正文和敏感凭证
- [ ] SQLite 审计仓储只提供 append/get/list
- [ ] 诊断创建、运行、人工审核和知识治理动作可追踪
- [ ] 事件包含聚合 ID、动作、结果、前后状态和版本
- [ ] 审计写入失败不会伪装成完整成功
- [ ] 迁移可逆且历史数据保留

## 2. 6C-2 一致性扫描

- [ ] 正常数据无阻塞级 finding
- [ ] 能发现跨诊断引用、未知 Evidence、无人工确认的 confirmed
- [ ] 能发现非法版本和知识来源断链
- [ ] 扫描过程不修改任何聚合
- [ ] 输出 JSON 与 Markdown 报告

## 3. 6C-3 备份恢复

- [ ] 使用 SQLite backup API 生成一致快照
- [ ] manifest 包含 revision、大小、SHA-256 与时间
- [ ] checksum、integrity、revision 校验通过后才允许恢复
- [ ] Runtime 运行中恢复被拒绝
- [ ] 恢复失败不破坏当前数据库
- [ ] 恢复后诊断、Evidence、结论、审核、知识和审计均可读取

## 4. 总回归

- [ ] `uv run ruff check .`
- [ ] `uv run pytest`
- [ ] Phase 0～5 固定回归通过
- [ ] 乐观锁、重启恢复和跨域探针通过
- [ ] 无数据库临时文件和敏感信息进入 Git

