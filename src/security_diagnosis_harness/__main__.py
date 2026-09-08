"""应用入口：python -m security_diagnosis_harness。"""

from __future__ import annotations

import uvicorn

from security_diagnosis_harness.api.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
