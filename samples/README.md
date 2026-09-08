# samples

存放本地样例设备数据，供后续阶段的 `StaticDeviceGateway` Adapter 读取。

## Phase 0A 状态

Phase 0A 只交付工程骨架与领域模型，本目录暂不落样例数据。

## 计划（Phase 0B）

```text
samples/devices/
  camera-001.json        设备主数据
  snapshots/*.json       设备状态快照
  alarms/*.json          设备告警事件
  configs/*.json         设备配置快照
samples/knowledge/
  camera-black-screen-sop.md
```

## 约束

- 只允许存放脱敏后的假数据；
- 禁止出现真实设备 IP、账号、密码、Token、secret；
- 样例配置中的凭证字段必须以 `***REDACTED***` 形式出现。
