# Phase 10 授权设备试接入运行手册

## 1. 当前结论

当前仅完成 `authorization_dry_run`，没有完成真实设备联调。Simulator、dry-run 与
`authorized_device_e2e` 必须使用不同报告类型，禁止互相冒充。

## 2. 试接入前置门禁

1. 人工审批只读设备范围、环境别名、有效时间窗和最大调用次数；
2. 凭证只以外部 `credential_ref` 提供，不写入仓库、日志或报告；
3. HTTP Adapter 必须在发出请求前调用授权预检；deny 时不得建立连接；
4. 禁止设备写操作、自动重试、自动通知和模型自动 confirmed；
5. 先运行固定测试、Phase 10 Gate 与授权 dry-run；任一失败立即停止。

## 3. 正式执行与证据

真实试接入必须由用户单次明确授权，并记录代码提交、授权清单 ID、聚合指标和人工
复核结论。报告不得保存 endpoint、IP、设备 ID、凭证、原始响应或生物特征。

## 4. 失败与回滚

授权缺失、过期、越界、预算耗尽、超时、限流或 Adapter 不可用时停止；不自动重试。
撤销外部凭证引用、禁用资产、关闭 HTTP Adapter，并保留脱敏聚合审计记录。SQLite
恢复沿用 Phase 6C 的已验收备份恢复流程。

## 5. 真实联调报告空模板

- report_kind: `authorized_device_e2e`
- code_commit / suite_version / configuration_hash
- authorization_manifest_id
- aggregate completion / degradation / evidence compliance
- device_writes: `0`
- external_notifications: `0`
- human_reviewer / decision

此模板为空不代表真实联调已完成。
