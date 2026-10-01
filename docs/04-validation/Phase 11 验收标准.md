# Phase 11 验收标准

## 1. Phase 11A

- [x] ONVIF Adapter 只允许受控 endpoint 与 host allowlist
- [x] 凭证只由 CredentialResolverPort 在调用边界解析
- [x] DeviceInformation、Profiles、StreamUri、RTSP 探活可映射到既有领域对象
- [x] 认证、超时、不可用、非法响应和缺 Profile 为稳定受控错误
- [x] 异常、Evidence、日志与报告不含密码、Nonce、完整 URI 或原始 SOAP
- [x] 无 ONVIF/设备写操作，无自动重试或 fallback

## 2. Phase 11B

- [x] 四个跨来源案例进入正式 Runner、Registry、Gateway、Evidence、Citation 链路
- [x] RTSP 失败不误判为设备离线
- [x] 平台拉流失败能被设备侧正常事实排除设备离线候选
- [x] 部分事实不被伪造成确定事实，失败调用不生成 Evidence
- [x] 黑屏结论引用受控码流事实（其中同时包含健康状态与内容异常）
- [x] confirmed 仍只能由 HumanReview 产生

## 3. Phase 11C

- [x] 八场景连续十轮 80/80，无随机失败
- [x] 每轮前后 toxic 和 Proxy 状态均恢复
- [x] 全量 pytest、ruff、diff check 和安全扫描通过
- [x] 停止后无容器、进程、凭证和运行时 ONVIF 配置残留
- [x] 报告明确标记 simulator_stability，不冒充真机联调

稳定门禁实测：10/10 轮，场景断言 80/80，完整 Agent Loop 40/40；每轮
`before_clean=true` 且 `after_clean=true`，未调用外部模型或真实设备。
封版全量回归为 1810 passed；停止后 `contract/onvif/toxiproxy=false`，运行时凭证、
ONVIF 配置与状态文件残留数为 0，Device Lab 容器残留数为 0。

## 4. Phase 11D 与封版

- [x] 用户明确授权设备、时间窗、只读范围和最大调用次数
- [x] Wi-Fi 真机完成发现、认证、Profile、Stream URI 与 RTSP Digest 最小联调
- [x] 真机报告已脱敏并与 Simulator 报告分开
- [x] 无写操作、无自动重试、无真实凭证入库或入 Git
- [x] 明确真机最小协议验收与生产上线、长期稳定、真实诊断准确率的边界
- [ ] 完成最终面试材料与教学路线，项目停止功能堆砌

详细证据见[Phase 11D 真机验收记录](./Phase%2011D%20真机验收记录.md)。
最终代码与边界审计见
[Phase 11 最终封版审计](../05-audits/2026-09-18-Phase11最终封版审计.md)。

## 5. 2026-09-19 封版后回归口径

知识治理与 Runtime 封装修复不要求重复 Phase 11D 真机验收。最终回归只运行离线测试、Simulator
与不访问 BGE/LLM/外部网络的固定脚本；已接入的真实摄像头不得因软件回归被隐式访问。原 1810
passed 保留为封版时点证据，新提交以新的全量回归记录为准。

## 6. 2026-09-29 安全与自洽性修复验收

本节新增验收项，不修改第 1～5 节的历史结论。修复范围与决策见
[ADR-002](../01-architecture/ADR-002安全边界强化.md)，逐项反例与证据见
[2026-09-19 审计报告第 6 节](../05-audits/2026-09-19知识治理与Runtime封装逆向审计.md)
与[交接报告](../05-audits/2026-09-29安全与自洽性修复交接报告.md)。

- [x] 公开调用方无法通过 Runtime 公开入口 `rotate()` 替换授权清单或重置预算
- [x] 容器关闭后，关闭前取得的授权快照视图仍不能旁路（无 rotate/authorize/close）
- [x] Runner 返回 timeout 后底层模型/工具操作停止：副作用计数不再变化、无 Evidence、无重试、无遗留 worker（**2026-10-01 修订**：该条只在"参与者已声明协作取消契约"时成立；未声明的参与者在严格模式下被拒绝执行，见第 7 节）
- [x] 评测历史存在可持久化可信 Head（跨进程重启可加载）
- [x] 历史回滚 / 截尾 / Head 缺失 / Head 篡改均被拒绝；完整性密钥不落盘
- [x] 两个实例并发 append 不丢失更新，两条记录都存在且 baseline 血缘合法
- [x] 发布中断（head 已推进 / 未推进）均可收敛到一致状态
- [x] 真实模型响应 usage 缺失或非法时 fail closed，不退化为 0 token / 0 cost
- [x] 真实模型响应有字节与正文上限，且仍保持每案例一次请求、无重试
- [x] 受控错误不含响应正文、API key 或 endpoint
- [x] BGE Adapter 有 host/scheme/port allowlist，禁止 userinfo/query/fragment
- [x] BGE Adapter 有输入与响应边界，并拒绝 NaN/Infinity/字符串/布尔/非法 index
- [x] BGE Adapter 有稳定错误与 close/上下文管理器，且未接入正式 Runtime
- [x] 正式 API 入口即使监听 localhost 也要求 operator/reviewer 双凭证
- [x] 正式 API 的 actor 只能来自认证 principal
- [x] operator 不能 review、reviewer 不能创建/运行、同一 actor 不能自审
- [x] 无认证 Demo 必须显式开启

验证口径：全量 `uv run pytest` 1897 passed；`uv run ruff check .` 通过；
`git diff --check 5d6c1ae..HEAD` 通过；离线 Phase 回归（Phase 0 demo、授权 dry-run、
Phase 8C 历史趋势、Phase 6 一致性、import 副作用探针）退出码均为 0。
本轮未运行真实模型、真实 BGE、真机探针与联网 Bootstrap。

## 7. 2026-10-01 跟进修复验收

本节是对第 6 节的**跟进复核与修订**，不删除第 1～6 节的历史结论。复核结论：第 6 节有 4 条
"测试通过但边界未闭环"，已在 `44409c9` / `378f87a` / `6221e0d` / `8ebde01` 修复。

- [x] 未声明协作取消契约的模型 / 工具在严格模式下**根本不被调用**（无副作用、无 worker、无子进程）
- [x] 声明了却违约的参与者被以 `cancellation_confirmed=False` + "取消未确认" 显式上报，不伪装成干净超时
- [x] 正式装配的 `FakeLLM` 与全部工具都有声明契约的守卫测试
- [x] 响应体上限在**传输期**生效：`client.stream` + `iter_bytes` 逐块累计，超限立刻停止读取并关闭响应
- [x] 恰好等于上限允许；超出 1 字节拒绝；首块超限只读 1 块；累计超限不读剩余块
- [x] 超限响应不进入 JSON 解析，错误不含 endpoint / 正文 / 凭证，注入 client 不被关闭
- [x] `create_app()` 与 `create_diagnoses_router()` 都拒绝"认证 + Demo"与"两者都缺"的组合
- [x] 只要存在已认证 principal，正文自报 reporter/reviewer 结构上不可能生效
- [x] Head 文件有独立的 `.head-lock` 跨进程排它锁，`load_head` / `save_head` 全周期持锁
- [x] 两个进程、三进程压力循环、两个历史共享一个 Head Store 都不丢更新，无 staging 残留，无死锁

验证口径（2026-10-01）：全量 `uv run pytest` **1933 passed**；`uv run ruff check .` 通过；
`git diff --check 5d6c1ae..HEAD` 通过；离线 Phase 回归 22 个脚本全部退出码 0（未运行脚本及原因见
[交接报告第 11 节](../05-audits/2026-09-29安全与自洽性修复交接报告.md)）。
本轮同样未运行真实模型、真实 BGE、真机探针与联网 Bootstrap，未 push。

**仍未闭环（明确保留）**：Python 无法抢占式终止正在运行的线程，因此取消是协作式的；直接调用
私有 `_within_deadline` 绕过准入闸门时，未声明参与者的迟到副作用仍无法阻止。
