"""启动本地 API 服务（Phase 0A 只提供 /health）。"""

from __future__ import annotations

import uvicorn

from security_diagnosis_harness.api.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
