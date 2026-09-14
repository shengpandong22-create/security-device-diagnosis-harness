<# Controlled A/B measurement for LLM polling. Calls CodeBuddy twice and Codex four times. #>

param(
    [string]$OutputRoot = "",
    [string]$CodeBuddyModel = "deepseek-v4.1-flash",
    [string]$CodexModel = "gpt-5.6-luna"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_process.ps1")

if (-not $OutputRoot) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputRoot = Join-Path $PSScriptRoot "..\docs\experiments\codex-codebuddy-usage\raw\phase2a-$stamp"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$codebuddy = Join-Path $env:APPDATA "npm\codebuddy.cmd"
$codex = (Get-Command codex).Source
if (-not (Test-Path $codebuddy)) { throw "Signed npm CodeBuddy entry not found" }

function New-FixtureRepo {
    param([string]$Variant)
    $root = Join-Path $OutputRoot "workspace-$Variant"
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    @'
import re


def normalize_asset_alias(value: str) -> str:
    """Return a lower-case safe alias or raise ValueError."""
    raise NotImplementedError
'@ | Set-Content (Join-Path $root "asset_alias.py") -Encoding UTF8
    @'
import unittest
from asset_alias import normalize_asset_alias


class AliasTests(unittest.TestCase):
    def test_normalizes_case_and_outer_space(self): self.assertEqual(normalize_asset_alias("  Camera-A  "), "camera-a")
    def test_preserves_dot_underscore_dash(self): self.assertEqual(normalize_asset_alias("Site_1.Cam-A"), "site_1.cam-a")
    def test_rejects_blank(self):
        with self.assertRaises(ValueError): normalize_asset_alias("   ")
    def test_rejects_slash(self):
        with self.assertRaises(ValueError): normalize_asset_alias("site/camera")
    def test_rejects_space_inside(self):
        with self.assertRaises(ValueError): normalize_asset_alias("site camera")
    def test_rejects_more_than_64_chars(self):
        with self.assertRaises(ValueError): normalize_asset_alias("a" * 65)
    def test_rejects_leading_dot(self):
        with self.assertRaises(ValueError): normalize_asset_alias(".camera")
    def test_rejects_leading_underscore(self):
        with self.assertRaises(ValueError): normalize_asset_alias("_camera")
    def test_rejects_leading_dash(self):
        with self.assertRaises(ValueError): normalize_asset_alias("-camera")


if __name__ == "__main__": unittest.main()
'@ | Set-Content (Join-Path $root "test_asset_alias.py") -Encoding UTF8
    "__pycache__/" | Set-Content (Join-Path $root ".gitignore") -Encoding ASCII
    git -C $root init -q
    git -C $root config user.name "Usage Experiment"
    git -C $root config user.email "usage-experiment@invalid.local"
    git -C $root add .
    git -C $root commit -q -m "test: define alias normalization contract"
    return [pscustomobject]@{
        Root = $root
        BaseCommit = (git -C $root rev-parse HEAD).Trim()
        BaseTree = (git -C $root rev-parse 'HEAD^{tree}').Trim()
    }
}

function Test-FixtureCompletion {
    param([string]$Root, [string]$BaseCommit)
    Push-Location $Root
    try {
        python -m unittest -v 2>&1 | Out-Null
        $testsPassed = $LASTEXITCODE -eq 0
    } finally { Pop-Location }
    $head = (git -C $Root rev-parse HEAD).Trim()
    $status = @(git -C $Root status --porcelain)
    return [pscustomobject]@{
        TestsPassed = $testsPassed
        CommitCreated = $head -ne $BaseCommit
        WorktreeClean = $status.Count -eq 0
        Head = $head
    }
}

function Start-CodeBuddyJob {
    param([string]$Root, [string]$Variant)
    $prompt = @(
        "Implement normalize_asset_alias in asset_alias.py.",
        "Exact rules: strip outer whitespace, lowercase, length 1..64, first character a-z, remaining characters only a-z 0-9 dot underscore dash.",
        "Raise ValueError for invalid input. Do not change tests.",
        "Run python -m unittest -v, then git add asset_alias.py and commit with message feat: normalize asset aliases.",
        "Do not access network tools or any files outside this fixture repository."
    ) -join [Environment]::NewLine
    Start-Job -ScriptBlock {
        param($Cli, $Model, $WorkingRoot, $Prompt, $Log)
        Set-Location $WorkingRoot
        $output = & $Cli -p -y --model $Model --allowedTools "Read,Write,Edit,Bash,Grep,Glob" --max-turns 8 --output-format text $Prompt 2>&1 | Out-String
        $output | Set-Content $Log -Encoding UTF8
        [pscustomobject]@{ exit_code = $LASTEXITCODE; output = $output }
    } -ArgumentList $codebuddy, $CodeBuddyModel, $Root, $prompt, (Join-Path $OutputRoot "codebuddy-$Variant.log.txt")
}

function Invoke-CodexJson {
    param([string[]]$Arguments, [string]$Root, [string]$LogName)
    $result = Invoke-ExternalWithExitEvent -FilePath $codex -CommandArguments $Arguments -WorkingDirectory $Root -TimeoutSeconds 300
    $result.Output | Set-Content (Join-Path $OutputRoot $LogName) -Encoding UTF8
    if ($result.ExitCode -ne 0) { throw "Codex call failed: $LogName" }
    return $result.Output
}

function Get-ThreadId {
    param([string]$JsonLines)
    foreach ($line in ($JsonLines -split "`r?`n")) {
        try { $item = $line | ConvertFrom-Json } catch { continue }
        if ($item.type -eq "thread.started") { return [string]$item.thread_id }
    }
    throw "thread.started not found"
}

function Invoke-Review {
    param([string]$Root, [string]$Variant)
    $prompt = "Review HEAD relative to HEAD~1. Verify normalize_asset_alias strips outer whitespace, lowercases, requires length 1..64, requires first character a-z, and permits only a-z 0-9 dot underscore dash afterwards. Run python -m unittest -v, then answer APPROVED or REJECTED with one sentence. Do not modify files."
    Invoke-CodexJson @("exec", "--json", "--model", $CodexModel, "--sandbox", "read-only", "--cd", $Root, $prompt) $Root "codex-$Variant-review.jsonl" | Out-Null
}

$a = New-FixtureRepo "A"
$jobA = Start-CodeBuddyJob $a.Root "A"
$pollPrompt = "Read only git status --short in this fixture. Reply RUNNING if asset_alias.py is uncommitted or still contains NotImplementedError; otherwise reply COMPLETED. Do not modify files."
$poll1 = Invoke-CodexJson @("exec", "--json", "--model", $CodexModel, "--sandbox", "read-only", "--cd", $a.Root, $pollPrompt) $a.Root "codex-A-poll-1.jsonl"
$pollThread = Get-ThreadId $poll1
Start-Sleep -Seconds 2
Invoke-CodexJson @("exec", "resume", "--json", "--model", $CodexModel, $pollThread, $pollPrompt) $a.Root "codex-A-poll-2.jsonl" | Out-Null
$doneA = Wait-Job $jobA -Timeout 600
if ($null -eq $doneA) { Stop-Job $jobA; throw "CodeBuddy A timeout" }
$resultA = Receive-Job $jobA; Remove-Job $jobA -Force
if ($resultA.exit_code -ne 0) { throw "CodeBuddy A failed" }
$completionA = Test-FixtureCompletion $a.Root $a.BaseCommit
if (-not ($completionA.TestsPassed -and $completionA.CommitCreated -and $completionA.WorktreeClean)) {
    throw "CodeBuddy A did not satisfy completion gates"
}
Invoke-Review $a.Root "A"

$b = New-FixtureRepo "B"
$jobB = Start-CodeBuddyJob $b.Root "B"
$doneB = Wait-Job $jobB -Timeout 600
if ($null -eq $doneB) { Stop-Job $jobB; throw "CodeBuddy B timeout" }
$resultB = Receive-Job $jobB; Remove-Job $jobB -Force
if ($resultB.exit_code -ne 0) { throw "CodeBuddy B failed" }
$completionB = Test-FixtureCompletion $b.Root $b.BaseCommit
if (-not ($completionB.TestsPassed -and $completionB.CommitCreated -and $completionB.WorktreeClean)) {
    throw "CodeBuddy B did not satisfy completion gates"
}
Invoke-Review $b.Root "B"

$summary = [ordered]@{
    experiment_id = Split-Path $OutputRoot -Leaf
    completed_at = [DateTimeOffset]::Now.ToString("o")
    base_contract_equal = $a.BaseTree -eq $b.BaseTree
    codebuddy_model = $CodeBuddyModel
    codebuddy_max_turns = 8
    codex_model = $CodexModel
    variant_a_poll_session_id = $pollThread
    variant_a_codex_calls = 3
    variant_b_codex_calls = 1
    raw_logs = @("codex-A-poll-1.jsonl", "codex-A-poll-2.jsonl", "codex-A-review.jsonl", "codex-B-review.jsonl")
}
$summary | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $OutputRoot "experiment.json") -Encoding UTF8
$summary | ConvertTo-Json -Depth 4
