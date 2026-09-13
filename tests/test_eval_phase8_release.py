from __future__ import annotations

import json

from scripts import eval_phase8_dataset_release as script


def test_phase8_release_script_is_repeatable_and_offline(capsys):
    assert script.main() == 0
    first = json.loads(capsys.readouterr().out)
    report_path = script.ROOT / first["json_report"]
    first_report = report_path.read_text(encoding="utf-8")
    assert script.main() == 0
    second = json.loads(capsys.readouterr().out)
    second_report = report_path.read_text(encoding="utf-8")
    assert first == second
    assert first_report == second_report
    assert first["released_version"] == "1.1.0"
    assert first["total_case_count"] == 7
    assert first["split_counts"] == {"dev": 3, "validation": 2, "test": 2}
    assert first["synthetic_case_count"] == 1
    assert first["authorized_case_count"] == 0
    assert first["test_set_remains_sealed"] is True
    assert first["external_model_called"] is False
