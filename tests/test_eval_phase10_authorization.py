from scripts.eval_phase10_authorization_dry_run import evaluate, main


def test_authorization_dry_run_closes_allow_and_deny_paths(capsys):
    assert main() == 0
    assert '"passed": true' in capsys.readouterr().out


def test_authorization_dry_run_never_claims_real_integration():
    result = evaluate()
    assert result["report_kind"] == "authorization_dry_run"
    assert result["real_device_called"] is False
    assert result["external_network_accessed"] is False
    assert result["device_write_attempted"] is False
    assert result["automatic_retry"] is False


def test_authorization_dry_run_preserves_budget_on_denial():
    result = evaluate()
    assert result["allow_path"] is True
    assert result["budget_deny_reason"] == "total_budget_exhausted"
    assert result["missing_manifest_reason"] == "missing_manifest"
    assert result["deny_preserved_budget"] is True
