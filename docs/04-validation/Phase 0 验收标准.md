# Phase 0 验收标准

> 本文档定义 Phase 0 完成标准。后续实现代码时，必须以这里为验收口径。

## 1. 工程验收

- [ ] 项目可通过 `uv sync` 安装依赖；
- [ ] `uv run pytest` 全绿；
- [ ] `uv run ruff check .` 无错误；
- [ ] `.env`、API Key、Token、设备凭证不会进入 Git；
- [ ] README 能说明项目定位、运行方式和安全边界。

## 2. 领域验收

- [ ] `SecurityDiagnosisCase` 能表达一次设备诊断；
- [ ] 状态机禁止非法跳转；
- [ ] `confirmed` 只能由人工确认动作产生；
- [ ] `SecurityFaultType` 至少包含 `camera_black_screen`；
- [ ] 领域层不依赖 FastAPI、SQLAlchemy、HTTP 客户端或具体 LLM SDK。

## 3. 工具验收

- [ ] 所有工具必须通过 Tool Registry 注册；
- [ ] 未注册工具必须拒绝；
- [ ] 无权限工具必须拒绝；
- [ ] 非法参数必须拒绝；
- [ ] 工具必须声明风险等级；
- [ ] Phase 0 所有设备工具均为 READ_ONLY；
- [ ] 工具失败不能伪造成 Evidence。

## 4. Evidence 验收

- [ ] 设备状态、告警事件、配置快照可以转换为 Evidence；
- [ ] Evidence 必须属于某个 Diagnosis；
- [ ] Evidence 有类型、来源、hash、可信度、脱敏状态；
- [ ] 相同诊断下同内容按 hash 去重；
- [ ] 结论引用的 Evidence ID 必须属于当前诊断；
- [ ] 只引用知识库 SOP 时，结论最多为 possible；
- [ ] probable 至少引用一个设备事实 Evidence。

## 5. 安全验收

- [ ] 原始密码、Token、secret、连接串不能原样入库；
- [ ] LLM 不能看到未脱敏敏感内容；
- [ ] LLM 不能直接执行设备写操作；
- [ ] Phase 0 不访问真实设备网络；
- [ ] 自动测试不调用真实模型。

## 6. Demo 验收

- [ ] 摄像头黑屏 demo 可一键运行；
- [ ] demo 使用 Fake LLM 或固定模型响应；
- [ ] demo 输出 diagnosis_id；
- [ ] demo 输出 Evidence 数量；
- [ ] demo 输出候选结论；
- [ ] demo 输出 Markdown 报告路径；
- [ ] demo 不依赖真实设备、不依赖真实外部模型。

## 7. 面试验收

学完 Phase 0 后，应能回答：

1. 为什么这个项目不叫普通 ChatBot？
2. Harness 约束了 LLM 哪些自由？
3. 设备状态、告警、配置分别能证明什么？
4. 为什么告警存在不能直接等于根因？
5. 为什么 confirmed 必须人工确认？
6. 旧应用诊断项目和新安防项目是什么关系？
7. 企业落地还需要补哪些系统？

## 8. Definition of Done

Phase 0 完成时，至少满足：

- [ ] 有一条摄像头黑屏端到端诊断链路；
- [ ] 有至少 15 个单元/集成测试；
- [ ] 有一份验收记录；
- [ ] 有一份面试讲解材料；
- [ ] Git 工作区干净；
- [ ] 已推送到 GitHub。
