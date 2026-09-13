<#
.SYNOPSIS
Verify event-driven CodeBuddy to Codex handoff with a harmless 1+1 task.

.DESCRIPTION
CodeBuddy must write DONE.json and exit. The orchestrator waits for the process Exited
event, validates the handoff envelope, and only then starts a read-only Codex review.
No repository source file, git branch, worktree, remote service, or credential is used.
#>

param(
    [string]$ProbeRoot = "D:\AgentStudy\agent-collab-event-probe-runs",
    [string]$CodeBuddyModel = "fast-model",
    [string]$CodexModel = "gpt-5.6-luna",
    [int]$CodeBuddyTimeoutSeconds = 300,
    [int]$CodexTimeoutSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_process.ps1")

function Resolve-CommandPath {
    param([string]$Name, [string]$Fallback = "")
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    if ($Fallback -and (Test-Path -LiteralPath $Fallback)) { return $Fallback }
    throw "Command not found: $Name"
}

function Assert-ProcessResult {
    param($Result, [string]$Step)
    if (
        $Result.TimedOut -or
        $Result.ExitCode -ne 0 -or
        $Result.CompletionSignal -ne "process_exited_event"
    ) {
        throw "$Step failed: exit=$($Result.ExitCode), signal=$($Result.CompletionSignal)"
    }
}

$codebuddy = Resolve-CommandPath "codebuddy" (
    Join-Path $env:LOCALAPPDATA "codebuddy\bin\codebuddy.exe"
)
$codex = Resolve-CommandPath "codex"
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$taskId = "arithmetic-$runId"
$probeDir = Join-Path $ProbeRoot $taskId
New-Item -ItemType Directory -Force -Path $probeDir | Out-Null

$donePath = Join-Path $probeDir "DONE.json"
$reviewPath = Join-Path $probeDir "REVIEW.json"
$codebuddyLog = Join-Path $probeDir "CODEBUDDY.log.txt"
$codexLog = Join-Path $probeDir "CODEX.log.txt"
$summaryPath = Join-Path $probeDir "SUMMARY.json"

$codebuddyPrompt = @(
    "This is an event handoff probe in an isolated empty directory.",
    "Calculate 1+1.",
    "Write DONE.json in the current directory as strict JSON with exactly these fields:",
    "protocol_version: string 1",
    "task_id: string $taskId",
    "status: string completed",
    "answer: integer 2",
    "Do not create other files. Do not run shell commands. Do not access network tools.",
    "After writing DONE.json, exit."
) -join [Environment]::NewLine

$codebuddyArgs = @(
    "-p",
    "-y",
    "--model", $CodeBuddyModel,
    "--allowedTools", "Read,Write,Edit",
    "--max-turns", "4",
    "--output-format", "text",
    $codebuddyPrompt
)
$codebuddyParameters = @{
    FilePath = $codebuddy
    CommandArguments = $codebuddyArgs
    WorkingDirectory = $probeDir
    TimeoutSeconds = $CodeBuddyTimeoutSeconds
}
$codebuddyResult = Invoke-ExternalWithExitEvent @codebuddyParameters
$codebuddyResult.Output | Set-Content -LiteralPath $codebuddyLog -Encoding UTF8
Assert-ProcessResult $codebuddyResult "CodeBuddy"

if (-not (Test-Path -LiteralPath $donePath)) {
    throw "CodeBuddy exited without DONE.json"
}
$done = Get-Content -LiteralPath $donePath -Raw -Encoding UTF8 | ConvertFrom-Json
if (
    $done.protocol_version -ne "1" -or
    $done.task_id -ne $taskId -or
    $done.status -ne "completed" -or
    [int]$done.answer -ne 2
) {
    throw "DONE.json failed protocol validation"
}

$codexPrompt = @(
    "Act as a read-only handoff verifier.",
    "Read $donePath.",
    "Verify task_id is $taskId, status is completed, and answer to 1+1 is integer 2.",
    "Return only strict JSON with protocol_version, task_id, status, expected_answer, actual_answer.",
    "status must be approved only if every check passes. Do not modify files."
) -join [Environment]::NewLine
$codexArgs = @(
    "exec",
    "--model", $CodexModel,
    "--sandbox", "read-only",
    "--skip-git-repo-check",
    "--color", "never",
    "--cd", $probeDir,
    "--output-last-message", $reviewPath,
    $codexPrompt
)
$codexParameters = @{
    FilePath = $codex
    CommandArguments = $codexArgs
    WorkingDirectory = $probeDir
    TimeoutSeconds = $CodexTimeoutSeconds
}
$codexResult = Invoke-ExternalWithExitEvent @codexParameters
$codexResult.Output | Set-Content -LiteralPath $codexLog -Encoding UTF8
Assert-ProcessResult $codexResult "Codex review"

if (-not (Test-Path -LiteralPath $reviewPath)) {
    throw "Codex exited without REVIEW.json"
}
$review = Get-Content -LiteralPath $reviewPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (
    $review.protocol_version -ne "1" -or
    $review.task_id -ne $taskId -or
    $review.status -ne "approved" -or
    [int]$review.expected_answer -ne 2 -or
    [int]$review.actual_answer -ne 2
) {
    throw "REVIEW.json failed protocol validation"
}

$summary = [ordered]@{
    passed = $true
    task_id = $taskId
    answer = 2
    codebuddy_exit_code = $codebuddyResult.ExitCode
    codebuddy_completion_signal = $codebuddyResult.CompletionSignal
    done_validated = $true
    codex_triggered_after_done = $true
    codex_exit_code = $codexResult.ExitCode
    codex_completion_signal = $codexResult.CompletionSignal
    review_status = $review.status
    probe_dir = $probeDir
}
$summary | ConvertTo-Json | Set-Content -LiteralPath $summaryPath -Encoding UTF8
$summary | ConvertTo-Json
exit 0
