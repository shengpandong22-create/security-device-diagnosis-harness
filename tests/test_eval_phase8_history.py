from __future__ import annotations

import json

from scripts import eval_phase8_history_trend as script


def test_phase8_history_script_is_offline_repeatable_and_gate_driven(capsys):
    assert script.main() == 0
    first = json.loads(capsys.readouterr().out)
    assert script.main() == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second
    assert first["comparable_run_count"] == 2
    assert first["candidate_gate_allowed"] is False
    assert first["history_contains_case_details"] is False
    assert first["external_model_called"] is False
