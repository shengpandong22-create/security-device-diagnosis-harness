# samples

存放本地样例设备数据，供 `StaticDeviceGateway`（`adapters/device_gateway/static.py`）读取。

## 现状（Phase 0B）

```text
samples/devices/
  static_devices.sample.json   StaticDeviceGateway 样例数据（摄像头黑屏场景）
```

文件结构：

```json
{
  "version": 1,
  "devices": [
    {
      "device_id": "camera-3f-001",
      "snapshot": {"online": true, "channel_online": true, "stream_status": "abnormal", "recording_status": "recording"},
      "alarms": [{"event_type": "STREAM_PUBLISH_FAILED", "severity": "critical", "message": "主码流发布失败"}],
      "config": {"enabled": true, "encoding": "H.265", "bitrate_kbps": 8192, "config": {"admin_password": "..."}}
    }
  ]
}
```

- `snapshot` 对应 `DeviceSnapshot`，`alarms` 对应 `DeviceAlarmEvent`，`config` 对应 `DeviceConfigSnapshot`；
- 缺失 `snapshot` / `config` 时网关抛 `DeviceGatewayDataError`，设备不存在时抛 `DeviceNotFoundError`；
- 单元测试默认使用 `tmp_path` 下的临时 JSON，只有一条测试校验本样例文件可加载。

## 计划（Phase 0C）

- 补充多个设备的样例数据；
- 补充 `samples/knowledge/` 下的 SOP 文本（当前 SOP 直接内置在 `tools/knowledge_search.py`）。

## 约束

- 只允许存放脱敏后的假数据；
- 禁止出现真实设备 IP、账号、密码、Token、secret；
- 样例配置中的凭证字段会被 `DeviceConfigSnapshot` 自动替换为 `***REDACTED***`。
