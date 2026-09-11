# Phase 6 开发总结与 Phase 7 计划

## 1. Phase 6 完成了什么

Phase 6 把项目从“内存中的可控诊断 Demo”推进为“具备单机数据可靠性基线的诊断服务”。

### 6A：持久化基座

- Diagnosis 与 Knowledge 通过稳定 Repository Port 隔离基础设施；
- SQLAlchemy + SQLite 保存完整聚合，Alembic 管理 schema；
- Domain 与 ORM 双向转换不丢枚举、时间和引用关系；
- 统一脱敏覆盖自由文本、嵌套 payload 和构造后修改；
- Repository 不向应用层泄漏 ORM 异常。

### 6B：正式运行与并发冲突检测

- RuntimeContainer 显式拥有 Engine 生命周期；
- 正式入口可自动迁移并在重启后恢复诊断聚合；
- 运行时能力闸门阻止摄像头专用 Runtime 处理其他故障域；
- Diagnosis 与 Knowledge 使用 version + CAS 拒绝陈旧副本覆盖；
- 冲突不会自动重放 Agent 或 HumanReview。

### 6C：数据可靠性闭环

- 追加式 AuditEvent 记录诊断和知识治理动作及版本变化；
- ConsistencyScanner 只读检查状态、引用、版本和来源链；
- SQLite backup API 创建一致快照并生成 checksum manifest；
- 恢复前验证 checksum、integrity、revision 和 Runtime owner；
- 恢复前保留 recovery copy，失败不覆盖当前数据库。

## 2. Phase 6 明确没有解决的问题

- 审计事件与业务聚合尚未使用同一数据库事务，审计失败会显式暴露，但可能形成“业务已写入、审计未写入”；
- SQLite 乐观锁不是分布式一致性或完全并发安全；
- 正式 Runtime 仍只启用摄像头黑屏能力；
- 没有生产级 PostgreSQL、任务队列、多租户、RBAC 或远程灾备；
- 固定案例规模较小，尚不足以证明诊断泛化能力。

这些边界必须在面试与文档中如实说明。

## 3. Phase 7 的核心问题

Phase 7 不再问“还能增加什么功能”，而是回答：

> 一次 Prompt、规则、工具或检索策略修改后，Agent 究竟进步了还是退步了？

因此下一阶段建设分层数据集、步骤级 Grader、版本基线与发布门禁。重点关注错误定位、证据质量、工具行为和成本，而不是只看最终标签。

## 4. 推荐实施顺序

1. 7A：数据集协议与 Dev/Validation/Test 隔离；
2. 7B：代码规则 Grader 与步骤级轨迹评分；
3. 7C：版本基线、差异报告和回归门禁；
4. 7D：低频真实模型评测和人工争议复核。
