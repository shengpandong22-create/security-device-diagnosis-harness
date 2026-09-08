"""统一 JSON 响应结构（Phase 0A 简化版）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiResponse[T](BaseModel):
    """统一响应信封。"""

    model_config = ConfigDict(extra="forbid")

    code: str = "ok"
    message: str = "ok"
    data: T


class HealthData(BaseModel):
    """健康检查载荷。"""

    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    service: str = "security-diagnosis-harness"
    version: str
    phase: str = "0A"
