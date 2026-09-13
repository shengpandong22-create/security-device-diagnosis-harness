"""仅供本机契约测试使用的只读 Security Platform 服务。"""

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import SecretStr

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.domain.camera import StreamKind
from security_diagnosis_harness.ports.device_gateway import DeviceGatewayError


def create_contract_app(data_path: str | Path, expected_credential: SecretStr) -> FastAPI:
    """创建只读契约服务；不应作为生产 API 启动。"""
    gateway = StaticDeviceGateway(data_path)
    app = FastAPI(title="Local Security Platform Contract", docs_url=None, redoc_url=None)

    def authorize(authorization: str | None) -> None:
        expected = f"Bearer {expected_credential.get_secret_value()}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="authentication_failed")

    def invoke(authorization: str | None, callback):
        authorize(authorization)
        try:
            result = callback()
        except DeviceGatewayError as exc:
            raise HTTPException(status_code=404, detail="device_fact_unavailable") from exc
        if isinstance(result, list):
            return [item.model_dump(mode="json") for item in result]
        return result.model_dump(mode="json")

    @app.get("/v1/devices/{device_id}/status")
    def status(device_id: str, authorization: str | None = Header(default=None)):
        return invoke(authorization, lambda: gateway.query_status(device_id))

    @app.get("/v1/devices/{device_id}/channel")
    def channel(device_id: str, authorization: str | None = Header(default=None)):
        return invoke(authorization, lambda: gateway.query_channel_snapshot(device_id))

    @app.get("/v1/devices/{device_id}/streams/{stream_kind}")
    def stream(
        device_id: str, stream_kind: StreamKind, authorization: str | None = Header(default=None)
    ):
        return invoke(authorization, lambda: gateway.query_stream_snapshot(device_id, stream_kind))

    @app.get("/v1/devices/{device_id}/platform-pull")
    def platform_pull(device_id: str, authorization: str | None = Header(default=None)):
        return invoke(authorization, lambda: gateway.query_platform_pull_status(device_id))

    @app.get("/v1/devices/{device_id}/alarms")
    def alarms(
        device_id: str,
        keyword: str | None = None,
        limit: int = Query(default=10, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        return invoke(authorization, lambda: gateway.search_alarm_events(device_id, keyword, limit))

    @app.get("/v1/devices/{device_id}/config")
    def config(device_id: str, authorization: str | None = Header(default=None)):
        return invoke(authorization, lambda: gateway.read_config_snapshot(device_id))

    return app
