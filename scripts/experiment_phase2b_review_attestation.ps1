<# One-call Phase 2B validation of Codex reviewing a host-generated attestation. #>

param(
    [string]$OutputRoot = "",
    [string]$CodexModel = "gpt-5.6-luna"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_process.ps1")

if (-not $OutputRoot) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputRoot = Join-Path $PSScriptRoot "..\docs\experiments\codex-codebuddy-usage\raw\phase2b-$stamp"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$fixture = Join-Path $OutputRoot "workspace"
New-Item -ItemType Directory -Path $fixture -Force | Out-Null
$codex = (Get-Command codex).Source
$python = (Get-Command python).Source

function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

@'
def normalize_asset_alias(value: str) -> str:
    raise NotImplementedError
'@ | Set-Content (Join-Path $fixture "asset_alias.py") -Encoding UTF8
@'
import unittest
from asset_alias import normalize_asset_alias

class AliasTests(unittest.TestCase):
    def test_normalizes(self): self.assertEqual(normalize_asset_alias(" Camera-A "), "camera-a")
    def test_allowed_tail(self): self.assertEqual(normalize_asset_alias("a._-1"), "a._-1")
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

if __name__ == "__main__": unittest.main()
'@ | Set-Content (Join-Path $fixture "test_asset_alias.py") -Encoding UTF8
"__pycache__/" | Set-Content (Join-Path $fixture ".gitignore") -Encoding ASCII
git -C $fixture init -q
git -C $fixture config user.name "Usage Experiment"
git -C $fixture config user.email "usage-experiment@invalid.local"
git -C $fixture add .
git -C $fixture commit -q -m "test: define alias normalization contract"
$baseCommit = (git -C $fixture rev-parse HEAD).Trim()

@'
import re

_ALIAS_PATTERN = re.compile(r"[a-z][a-z0-9._-]{0,63}")

def normalize_asset_alias(value: str) -> str:
    normalized = value.strip().lower()
    if _ALIAS_PATTERN.fullmatch(normalized) is None:
        raise ValueError("invalid asset alias")
    return normalized
'@ | Set-Content (Join-Path $fixture "asset_alias.py") -Encoding UTF8

Push-Location $fixture
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $testOutput = & $python -m unittest -v 2>&1 | Out-String
    $testExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorAction
    Pop-Location
}
$match = [regex]::Match($testOutput, 'Ran (\d+) tests?')
$passedTests = if ($match.Success -and $testExitCode -eq 0) { [int]$match.Groups[1].Value } else { 0 }
if ($testExitCode -ne 0 -or $passedTests -ne 10) { throw "Host fixture validation failed" }
$changes = @(git -C $fixture status --porcelain | ForEach-Object { $_.Substring(3) })
if ($changes.Count -ne 1 -or $changes[0] -ne "asset_alias.py") { throw "Unexpected fixture changes" }

[ordered]@{
    schema_version = 1
    command = "python -m unittest -v"
    exit_code = $testExitCode
    passed_tests = $passedTests
    changed_files_before_commit = $changes
    base_commit = $baseCommit
    asset_alias_sha256 = Get-Sha256 (Join-Path $fixture "asset_alias.py")
    tests_sha256 = Get-Sha256 (Join-Path $fixture "test_asset_alias.py")
    generated_by = "experiment_harness"
} | ConvertTo-Json | Set-Content (Join-Path $fixture ".experiment-validation.json") -Encoding UTF8
git -C $fixture add -- asset_alias.py .experiment-validation.json
git -C $fixture commit -q -m "feat: normalize asset aliases"
if (@(git -C $fixture status --porcelain).Count -ne 0) { throw "Fixture is not clean" }

$prompt = @(
    "Review HEAD relative to HEAD~1 without modifying files.",
    "The requirement is: strip outer whitespace, lowercase, length 1..64, first character a-z, remaining characters only a-z 0-9 dot underscore dash.",
    "Inspect the implementation, test_asset_alias.py, and .experiment-validation.json.",
    "Recompute both SHA-256 values with read-only shell commands and verify the attested base commit and changed-file scope against Git.",
    "Do not execute Python or any host binary outside shell built-ins because the reviewer sandbox may block them.",
    "Your final line must start with exactly APPROVED or REJECTED, followed by one sentence."
) -join [Environment]::NewLine
$result = Invoke-ExternalWithExitEvent -FilePath $codex -CommandArguments @(
    "exec", "--json", "--model", $CodexModel, "--sandbox", "read-only", "--cd", $fixture, $prompt
) -WorkingDirectory $fixture -TimeoutSeconds 300
$reviewPath = Join-Path $OutputRoot "codex-review.jsonl"
$result.Output | Set-Content $reviewPath -Encoding UTF8
if ($result.ExitCode -ne 0) { throw "Codex review process failed" }

$finalMessage = $null
$usage = $null
$threadId = $null
foreach ($line in ($result.Output -split "`r?`n")) {
    try { $item = $line | ConvertFrom-Json } catch { continue }
    if ($item.type -eq "thread.started") { $threadId = [string]$item.thread_id }
    if ($item.type -eq "turn.completed") { $usage = $item.usage }
    if ($item.type -eq "item.completed" -and $item.item.type -eq "agent_message") {
        $finalMessage = [string]$item.item.text
    }
}
$approved = [bool]($finalMessage -and $finalMessage -match '(?m)^APPROVED\b')
$summary = [ordered]@{
    experiment_id = Split-Path $OutputRoot -Leaf
    executed_codebuddy = $false
    codex_calls = 1
    codex_model = $CodexModel
    review_approved = $approved
    review_process_exit_code = $result.ExitCode
    input_tokens = if ($usage) { [int64]$usage.input_tokens } else { $null }
    cached_input_tokens = if ($usage) { [int64]$usage.cached_input_tokens } else { $null }
    output_tokens = if ($usage) { [int64]$usage.output_tokens } else { $null }
    reasoning_output_tokens = if ($usage) { [int64]$usage.reasoning_output_tokens } else { $null }
    session_id = $threadId
    final_message = $finalMessage
}
$summary | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $OutputRoot "experiment.json") -Encoding UTF8
$summary | ConvertTo-Json -Depth 4
if (-not $approved) { exit 1 }
