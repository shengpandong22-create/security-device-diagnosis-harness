"""连续十轮执行 Device Lab 八场景与完整 Agent Loop 稳定门禁。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / ".device-lab" / "runtime"
_REPORT = _RUNTIME / "scenario-report.json"
_OUTPUT = _RUNTIME / "phase11-stability-report.json"
_CONTROL_URL = "http://127.0.0.1:28474"
_PROXIES = ("security-platform-contract", "onvif-rtsp")
_ROUNDS = 10


def _proxy_state(client: httpx.Client) -> dict[str, dict[str, object]]:
    state: dict[str, dict[str, object]] = {}
    for name in _PROXIES:
        proxy = client.get(f"/proxies/{name}")
        proxy.raise_for_status()
        toxics = client.get(f"/proxies/{name}/toxics")
        toxics.raise_for_status()
        state[name] = {
            "enabled": bool(proxy.json()["enabled"]),
            "toxic_names": sorted(item["name"] for item in toxics.json()),
        }
    return state


def _is_clean(state: dict[str, dict[str, object]]) -> bool:
    return all(item["enabled"] and not item["toxic_names"] for item in state.values())


def main() -> None:
    credential_path = _RUNTIME / "credential.txt"
    if not credential_path.exists():
        raise SystemExit("Device Lab 尚未启动")
    environment = os.environ.copy()
    environment["SECURITY_DIAGNOSIS_LAB_CREDENTIAL"] = credential_path.read_text().strip()
    rounds: list[dict[str, object]] = []

    with httpx.Client(base_url=_CONTROL_URL, timeout=5) as control:
        initial_state = _proxy_state(control)
        if not _is_clean(initial_state):
            raise SystemExit("稳定门禁启动前代理状态不干净，请先重新启动 Device Lab")
        for number in range(1, _ROUNDS + 1):
            before = _proxy_state(control)
            completed = subprocess.run(
                [sys.executable, "scripts/eval_device_lab_scenarios.py"],
                cwd=_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            after = _proxy_state(control)
            scenario_report = (
                json.loads(_REPORT.read_text(encoding="utf-8"))
                if _REPORT.exists()
                else {}
            )
            passed = (
                completed.returncode == 0
                and scenario_report.get("passed") == 8
                and scenario_report.get("agent_loop_passed") == 4
                and _is_clean(before)
                and _is_clean(after)
            )
            rounds.append(
                {
                    "round": number,
                    "passed": passed,
                    "scenario_passed": scenario_report.get("passed", 0),
                    "agent_loop_passed": scenario_report.get("agent_loop_passed", 0),
                    "before_clean": _is_clean(before),
                    "after_clean": _is_clean(after),
                    "exit_code": completed.returncode,
                    "stderr_tail": completed.stderr[-500:] if not passed else "",
                }
            )
            print(f"ROUND_{number}: {'PASS' if passed else 'FAIL'}", flush=True)
            if not passed:
                break

    report = {
        "report_kind": "simulator_stability",
        "rounds_required": _ROUNDS,
        "rounds_completed": len(rounds),
        "rounds_passed": sum(bool(item["passed"]) for item in rounds),
        "scenario_assertions": sum(int(item["scenario_passed"]) for item in rounds),
        "agent_loop_assertions": sum(int(item["agent_loop_passed"]) for item in rounds),
        "external_model_called": False,
        "real_device_accessed": False,
        "results": rounds,
    }
    _OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["rounds_passed"] != _ROUNDS or report["scenario_assertions"] != 80:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
