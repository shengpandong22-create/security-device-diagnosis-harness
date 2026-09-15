"""在宿主机启动 Phase 9 的本地只读 HTTP 契约服务。"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from security_diagnosis_harness.adapters.device_gateway.contract_service import (
    create_contract_app,
)
from security_diagnosis_harness.bootstrap.container import CAMERA_CASES_DATA_PATH

_CREDENTIAL_ENV = "SECURITY_DIAGNOSIS_LAB_CREDENTIAL"


def main() -> None:
    credential = os.environ.get(_CREDENTIAL_ENV)
    if not credential:
        raise SystemExit(f"缺少 {_CREDENTIAL_ENV}；Device Lab 不接受仓库内硬编码凭证")
    app = create_contract_app(
        Path(CAMERA_CASES_DATA_PATH),
        SecretStr(credential),
        enable_lab_fault_fixtures=True,
    )
    uvicorn.run(app, host="0.0.0.0", port=28082, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
