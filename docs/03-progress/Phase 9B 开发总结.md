# Phase 9B 开发总结

## 1. 完成范围

Phase 9B 完成本机 Security Platform 只读契约服务，以及首个供应商无关 HTTP
Adapter。当前覆盖设备状态、通道、码流、平台拉流、告警查询和配置摘要六类事实。

## 2. HTTP 安全边界

- 仅接受 HTTPS；`localhost`、`127.0.0.1`、`::1` 可使用 HTTP；
- base URL 禁止 userinfo、query 和 fragment，host 必须命中 allowlist；
- device_id 被编码为单一路径段，不能改变受控路由；
- 凭证通过 CredentialResolverPort 按调用解析，以 SecretStr 短暂持有；
- Client 不保存 Authorization 默认 Header，不自动跟随重定向；
- 设置连接、读取、写入和连接池阶段超时；
- 限制响应字节数、JSON 深度、列表数量，并执行 Pydantic Schema 校验；
- 401/403、超时、429、5xx 和协议错误映射为稳定错误分类；
- 不向上抛出底层 HTTP 异常文本，默认不进行任何自动重试；
- 不暴露写配置、设备重启或布撤防能力。

## 3. 验证口径

本阶段只执行 Phase 9B 定向测试以及既有 DeviceGateway/Tool 定向回归，不执行
全量测试。完整 Phase 0～9 回归推迟到 Phase 9C 路由和 Runtime 接入完成后执行。

测试使用进程内 FastAPI TestClient 与 HTTP Transport，不访问公网、真实设备或
真实模型。该结果只能称为“本机 HTTP 契约通过”，不能称为真实设备联调通过。

## 4. 下一步

Phase 9C 将实现资产目录、Adapter Registry、确定性 RoutedDeviceGateway，随后
依据已装配 capability 计算 Runtime 支持范围，并执行全量回归。
