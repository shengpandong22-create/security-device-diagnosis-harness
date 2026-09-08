# Security Device Diagnosis Harness

> 面向安防设备运维场景的可信诊断 Agent Harness。

本项目不是把服务日志丢给大模型生成答案，而是把 LLM 放入一个受控诊断 Harness：由确定性工程层负责设备事实采集、工具权限、Evidence 留存、引用校验、人工确认和评测回归，LLM 只在这些边界内完成推理。

## 项目定位

- 业务域：安防设备运维诊断，优先覆盖摄像头黑屏、录像缺失、门禁刷卡异常、报警误报等场景。
- 技术目标：验证 Agent 如何在设备状态、告警事件、配置快照、知识库 SOP 和人工反馈之间形成可信闭环。
- 当前阶段：文档基线，暂不开发业务代码。
- 重要边界：本项目不继承应用日志诊断主线，不迁移 Java Lab、NPE、服务日志、源码诊断、Gateway/Nacos/Trace 作为主叙事。

## 文档入口

- [项目定位与总体架构设计](./docs/00-overview/项目定位与总体架构设计.md)
- [旧项目能力复用矩阵](./docs/00-overview/旧项目能力复用矩阵.md)
- [架构图：安防设备诊断 Harness 总览](./docs/01-architecture/security-device-diagnosis-harness-overview.md)
- [Phase 0 实现规格说明](./docs/02-specifications/Phase%200%20实现规格说明.md)
- [Phase 0 验收标准](./docs/04-validation/Phase%200%20验收标准.md)

## 最小闭环路线

```text
用户描述设备故障
  -> 识别设备与故障类型
  -> 只读设备工具采集状态、告警、配置
  -> 转换为 Evidence
  -> 召回安防知识/SOP
  -> LLM 在 Harness 约束下推理
  -> Citation Policy 校验结论引用
  -> 人工确认/驳回/继续调查
  -> confirmed 后沉淀知识候选
```

## 安全约束

- 不提交 `.env`、API Key、Token、邮箱授权码、真实设备凭证。
- Phase 0 默认只使用 Fake LLM，不访问外部模型。
- Phase 0 只做只读设备事实采集，不做重启、配置下发、开门、静音等写操作。
