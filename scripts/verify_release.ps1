<# Offline, no-secret release verification matching the CI quality gate. #>

[CmdletBinding()]
param(
    [switch]$CheckEnvironmentOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$OutputRoot = Join-Path $RepoRoot "demo-output/verify-release"
$ForbiddenVariables = @(
    "SECURITY_DIAGNOSIS_EVAL_BASE_URL",
    "SECURITY_DIAGNOSIS_EVAL_MODEL",
    "SECURITY_DIAGNOSIS_EVAL_API_KEY",
    "SECURITY_DIAGNOSIS_EVAL_INTEGRITY_KEY",
    "SECURITY_DIAGNOSIS_DEVICE_HOST",
    "SECURITY_DIAGNOSIS_DEVICE_USERNAME",
    "SECURITY_DIAGNOSIS_DEVICE_PASSWORD",
    "SECURITY_DIAGNOSIS_DEVICE_RTSP_PORT",
    "SECURITY_DIAGNOSIS_LAB_CREDENTIAL",
    "SECURITY_DIAGNOSIS_API_TOKEN",
    "SECURITY_DIAGNOSIS_API_REVIEWER_TOKEN"
)

Push-Location $RepoRoot
try {
    $Commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "无法读取 Git commit" }
    $PythonVersion = (& uv run python --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "无法读取 Python 版本" }
    $UvVersion = (& uv --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "无法读取 uv 版本" }

    $Detected = @(
        $ForbiddenVariables | Where-Object {
            -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
        }
    )
    Write-Host "commit=$Commit"
    Write-Host "python=$PythonVersion"
    Write-Host "uv=$UvVersion"
    Write-Host "real_call_variables_detected=$($Detected.Count)"
    if ($Detected.Count -gt 0) {
        [Console]::Error.WriteLine(
            "拒绝离线验证：检测到真实调用变量（不显示值）：" +
            ($Detected -join ", ")
        )
        exit 2
    }
    if ($CheckEnvironmentOnly) {
        Write-Host "VERIFY_ENVIRONMENT_OK"
        exit 0
    }

    New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
    $Steps = @(
        @{ Name = "sync"; Command = { uv sync --frozen } },
        @{ Name = "ruff"; Command = { uv run ruff check . } },
        @{ Name = "pytest"; Command = { uv run pytest } },
        @{
            Name = "adversarial_round_1"
            Command = {
                uv run python scripts/eval_adversarial_cases.py `
                    --json-output "$OutputRoot/adversarial-round-1.json" `
                    --markdown-output "$OutputRoot/adversarial-round-1.md"
            }
        },
        @{
            Name = "adversarial_round_2"
            Command = {
                uv run python scripts/eval_adversarial_cases.py `
                    --json-output "$OutputRoot/adversarial-round-2.json" `
                    --markdown-output "$OutputRoot/adversarial-round-2.md"
            }
        },
        @{
            Name = "adversarial_stability"
            Command = {
                uv run python -c (
                    "import json; from pathlib import Path; " +
                    "from security_diagnosis_harness.evaluation.adversarial import AdversarialReport; " +
                    "root=Path(r'$OutputRoot'); " +
                    "a=AdversarialReport.model_validate(json.loads((root/'adversarial-round-1.json').read_text(encoding='utf-8'))); " +
                    "b=AdversarialReport.model_validate(json.loads((root/'adversarial-round-2.json').read_text(encoding='utf-8'))); " +
                    "assert a.semantic_payload()==b.semantic_payload(), 'adversarial semantic drift'; " +
                    "print('ADVERSARIAL_SEMANTIC_STABLE', a.semantic_hash())"
                )
            }
        },
        @{
            Name = "simulator_regression"
            Command = { uv run python scripts/eval_phase9a_simulator_e2e.py }
        }
    )
    $Passed = 0
    foreach ($Step in $Steps) {
        Write-Host "RUN $($Step.Name)"
        & $Step.Command
        if ($LASTEXITCODE -ne 0) {
            throw "验证步骤失败：$($Step.Name)，exit=$LASTEXITCODE"
        }
        $Passed += 1
        Write-Host "PASS $($Step.Name)"
    }
    Write-Host "VERIFY_RELEASE_OK steps=$Passed output=$OutputRoot"
} finally {
    Pop-Location
}
