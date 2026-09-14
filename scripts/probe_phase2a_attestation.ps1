<# Offline validation of the Phase 2A test-attestation protocol. No model or network calls. #>

param([string]$OutputPath = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $OutputPath) {
    $OutputPath = Join-Path $PSScriptRoot "..\docs\experiments\codex-codebuddy-usage\raw\phase2a-attestation-probe.json"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$python = (Get-Command python).Source
$root = Join-Path ([IO.Path]::GetTempPath()) ("phase2a-attestation-" + [guid]::NewGuid().ToString("N"))

function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-Attestation([string]$Fixture, [hashtable]$Attestation) {
    $expectedKeys = @(
        "schema_version", "command", "exit_code", "passed_tests",
        "changed_files_before_commit", "base_commit", "asset_alias_sha256",
        "tests_sha256", "generated_by"
    )
    if (@($expectedKeys | Where-Object { -not $Attestation.ContainsKey($_) }).Count -ne 0) { return $false }
    if ($Attestation.schema_version -ne 1 -or $Attestation.command -ne "python -m unittest -v") { return $false }
    if ($Attestation.exit_code -ne 0 -or $Attestation.passed_tests -ne 10) { return $false }
    if (@($Attestation.changed_files_before_commit).Count -ne 1 -or $Attestation.changed_files_before_commit[0] -ne "asset_alias.py") { return $false }
    $actualChanges = @(git -C $Fixture status --porcelain | ForEach-Object { $_.Substring(3) })
    if ($actualChanges.Count -ne 1 -or $actualChanges[0] -ne "asset_alias.py") { return $false }
    if ((git -C $Fixture rev-parse HEAD).Trim() -ne $Attestation.base_commit) { return $false }
    if ((Get-Sha256 (Join-Path $Fixture "asset_alias.py")) -ne $Attestation.asset_alias_sha256) { return $false }
    if ((Get-Sha256 (Join-Path $Fixture "test_asset_alias.py")) -ne $Attestation.tests_sha256) { return $false }
    return $Attestation.generated_by -eq "experiment_harness"
}

try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    @'
def normalize_asset_alias(value: str) -> str:
    raise NotImplementedError
'@ | Set-Content (Join-Path $root "asset_alias.py") -Encoding UTF8
    @'
import unittest
from asset_alias import normalize_asset_alias

class AliasTests(unittest.TestCase):
    def test_ok(self): self.assertEqual(normalize_asset_alias(" Camera-A "), "camera-a")
    def test_blank(self):
        with self.assertRaises(ValueError): normalize_asset_alias(" ")
    def test_slash(self):
        with self.assertRaises(ValueError): normalize_asset_alias("a/b")
    def test_space(self):
        with self.assertRaises(ValueError): normalize_asset_alias("a b")
    def test_long(self):
        with self.assertRaises(ValueError): normalize_asset_alias("a" * 65)
    def test_dot(self):
        with self.assertRaises(ValueError): normalize_asset_alias(".a")
    def test_underscore(self):
        with self.assertRaises(ValueError): normalize_asset_alias("_a")
    def test_dash(self):
        with self.assertRaises(ValueError): normalize_asset_alias("-a")
    def test_digit(self):
        with self.assertRaises(ValueError): normalize_asset_alias("1a")
    def test_allowed_tail(self): self.assertEqual(normalize_asset_alias("a._-1"), "a._-1")

if __name__ == "__main__": unittest.main()
'@ | Set-Content (Join-Path $root "test_asset_alias.py") -Encoding UTF8
    "__pycache__/" | Set-Content (Join-Path $root ".gitignore") -Encoding ASCII
    git -C $root init -q
    git -C $root config user.name "Attestation Probe"
    git -C $root config user.email "attestation-probe@invalid.local"
    git -C $root add .
    git -C $root commit -q -m "test: create attestation fixture"
    $baseCommit = (git -C $root rev-parse HEAD).Trim()
    @'
import re

_PATTERN = re.compile(r"[a-z][a-z0-9._-]{0,63}")

def normalize_asset_alias(value: str) -> str:
    normalized = value.strip().lower()
    if _PATTERN.fullmatch(normalized) is None:
        raise ValueError("invalid asset alias")
    return normalized
'@ | Set-Content (Join-Path $root "asset_alias.py") -Encoding UTF8

    Push-Location $root
    try {
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $testOutput = & $python -m unittest -v 2>&1 | Out-String
        $testExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
        Pop-Location
    }
    $countMatch = [regex]::Match($testOutput, 'Ran (\d+) tests?')
    $attestation = @{
        schema_version = 1
        command = "python -m unittest -v"
        exit_code = $testExitCode
        passed_tests = [int]$countMatch.Groups[1].Value
        changed_files_before_commit = @("asset_alias.py")
        base_commit = $baseCommit
        asset_alias_sha256 = Get-Sha256 (Join-Path $root "asset_alias.py")
        tests_sha256 = Get-Sha256 (Join-Path $root "test_asset_alias.py")
        generated_by = "experiment_harness"
    }

    $validAccepted = Test-Attestation $root $attestation
    $sourceTamper = $attestation.Clone(); $sourceTamper.asset_alias_sha256 = "0" * 64
    $testTamper = $attestation.Clone(); $testTamper.tests_sha256 = "0" * 64
    $exitTamper = $attestation.Clone(); $exitTamper.exit_code = 1
    $scopeTamper = $attestation.Clone(); $scopeTamper.changed_files_before_commit = @("asset_alias.py", "other.py")
    $result = [ordered]@{
        executed_real_models = $false
        valid_attestation_accepted = $validAccepted
        source_tamper_rejected = -not (Test-Attestation $root $sourceTamper)
        test_tamper_rejected = -not (Test-Attestation $root $testTamper)
        failed_exit_rejected = -not (Test-Attestation $root $exitTamper)
        scope_tamper_rejected = -not (Test-Attestation $root $scopeTamper)
        test_exit_code = $testExitCode
        passed_tests = [int]$countMatch.Groups[1].Value
    }
    New-Item -ItemType Directory -Path (Split-Path $OutputPath -Parent) -Force | Out-Null
    $result | ConvertTo-Json | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    $result | ConvertTo-Json
    if (@($result.Values | Where-Object { $_ -is [bool] -and -not $_ }).Count -ne 0) { exit 1 }
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
