<#
.SYNOPSIS
Run a minimal Codex -> CodeBuddy -> Codex handoff probe.

.DESCRIPTION
This is not the production overnight orchestrator. It is a small safety probe:

1. Codex creates a PLAN from a fixed probe requirement.
2. CodeBuddy works in an isolated git worktree and writes a tiny probe artifact.
3. Codex reviews the worktree diff and writes REVIEW.md.

The script never auto-merges and never pushes. It leaves the worktree and run directory for
inspection.
#>

param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Model = "fast-model",
    [int]$CodeBuddyMaxTurns = 8,
    [int]$CodexTimeoutSeconds = 900,
    [int]$CodeBuddyTimeoutSeconds = 900,
    [switch]$AllowDirty
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_process.ps1")

function Resolve-CommandPath {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Fallback
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    if ($Fallback -and (Test-Path $Fallback)) {
        return $Fallback
    }
    throw "Command not found: $Name"
}

function Invoke-ExternalWithTimeout {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$CommandArguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $job = Start-Job -ScriptBlock {
        param($FilePath, $CommandArguments, $WorkingDirectory)
        Set-Location -LiteralPath $WorkingDirectory
        $output = & $FilePath @CommandArguments 2>&1 | Out-String
        [pscustomobject]@{
            Output = $output
            ExitCode = $LASTEXITCODE
        }
    } -ArgumentList $FilePath, $CommandArguments, $WorkingDirectory

    $completed = Wait-Job -Job $job -Timeout $TimeoutSeconds
    if ($null -eq $completed) {
        Stop-Job -Job $job -ErrorAction SilentlyContinue
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        return [pscustomobject]@{
            Output = "TIMEOUT after $TimeoutSeconds seconds"
            ExitCode = 124
            TimedOut = $true
        }
    }

    $result = Receive-Job -Job $job
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        Output = [string]$result.Output
        ExitCode = [int]$result.ExitCode
        TimedOut = $false
    }
}

function Test-FalseSuccessOutput {
    param([string]$Text)

    $patterns = @(
        "Authentication required",
        "Please use /login",
        "429",
        "rate limit",
        "Do you want to proceed",
        "permission prompts are not available",
        "not recognized",
        "command not found",
        "Unknown command",
        "Max turns"
    )

    foreach ($pattern in $patterns) {
        if ($Text -match [regex]::Escape($pattern)) {
            return $pattern
        }
    }
    return $null
}

function Assert-ExternalResult {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$StepName
    )

    $falseSuccess = Test-FalseSuccessOutput $Result.Output
    if ($Result.TimedOut -or $Result.ExitCode -ne 0 -or $null -ne $falseSuccess) {
        throw "$StepName failed. exit=$($Result.ExitCode), timed_out=$($Result.TimedOut), false_success_pattern=$falseSuccess"
    }
}

$codebuddy = Resolve-CommandPath `
    -Name "codebuddy" `
    -Fallback (Join-Path $env:LOCALAPPDATA "codebuddy\bin\codebuddy.exe")
$codex = Resolve-CommandPath -Name "codex"

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$status = git -C $ProjectRoot status --short
if ($status -and -not $AllowDirty) {
    throw "Project worktree is not clean. Commit/stash changes first, or pass -AllowDirty. Current status:`n$status"
}

$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $ProjectRoot ".agent-collab\runs\orchestrator-probe-$runId"
$worktreeRoot = Join-Path (Split-Path $ProjectRoot -Parent) "security-device-diagnosis-harness-codebuddy-probe-$runId"
$branchName = "codex/probe-codebuddy-$runId"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$reqPath = Join-Path $runDir "REQ.md"
$planPath = Join-Path $runDir "PLAN.md"
$codebuddyLogPath = Join-Path $runDir "CODEBUDDY.log.txt"
$reviewPath = Join-Path $runDir "REVIEW.md"
$summaryPath = Join-Path $runDir "SUMMARY.md"

@(
    "# Orchestrator Probe Requirement",
    "",
    "Verify that Codex and CodeBuddy can complete a minimal handoff through files, git worktree isolation, and process exit codes.",
    "",
    "CodeBuddy must only create one file in the isolated worktree:",
    "",
    "CODEBUDDY_PROBE_RESULT.md",
    "",
    "The file content must include:",
    "",
    "- probe: ok",
    "- current working directory",
    "- one sentence saying no business code was modified",
    "",
    "Forbidden:",
    "",
    "- do not modify src/",
    "- do not modify tests/",
    "- do not commit Git",
    "- do not access network"
) -join [Environment]::NewLine | Set-Content -Path $reqPath -Encoding UTF8

$codexPlanPrompt = @(
    "You are Codex acting as designer and supervisor. Read the repository and the requirement file, then output a very small execution plan.",
    "",
    "Rules:",
    "- Do not modify any files.",
    "- Do not request real models, real devices, databases, RAG, or frontend work.",
    "- Make it explicit that CodeBuddy may only create CODEBUDDY_PROBE_RESULT.md in the isolated worktree.",
    "- Output Markdown.",
    "",
    "Requirement file: $reqPath"
) -join [Environment]::NewLine

$codexPlanArgs = @(
    "exec",
    "--sandbox", "read-only",
    "--color", "never",
    "--cd", $ProjectRoot,
    $codexPlanPrompt
)
$planResult = Invoke-ExternalWithExitEvent `
    -FilePath $codex `
    -CommandArguments $codexPlanArgs `
    -WorkingDirectory $ProjectRoot `
    -TimeoutSeconds $CodexTimeoutSeconds
$planResult.Output | Set-Content -Path $planPath -Encoding UTF8
Assert-ExternalResult -Result $planResult -StepName "Codex plan"

git -C $ProjectRoot worktree add -b $branchName $worktreeRoot HEAD | Out-Null

$codebuddyPrompt = @(
    "Execute the minimal probe task according to the plan.",
    "",
    "Plan file path: $planPath",
    "Requirement file path: $reqPath",
    "",
    "Hard rules:",
    "1. You should be working in this isolated worktree: $worktreeRoot",
    "2. Only create or overwrite CODEBUDDY_PROBE_RESULT.md at the worktree root.",
    "3. The file content must include probe: ok.",
    "4. Do not modify src/, tests/, or docs/.",
    "5. Do not commit git.",
    "6. Do not access network.",
    "7. After finishing, run git status --short and briefly summarize the result."
) -join [Environment]::NewLine

$codebuddyArgs = @(
    "-p",
    "-y",
    "--model", $Model,
    "--allowedTools", "Read,Write,Edit,Bash",
    "--max-turns", "$CodeBuddyMaxTurns",
    "--output-format", "text",
    $codebuddyPrompt
)
$codebuddyResult = Invoke-ExternalWithExitEvent `
    -FilePath $codebuddy `
    -CommandArguments $codebuddyArgs `
    -WorkingDirectory $worktreeRoot `
    -TimeoutSeconds $CodeBuddyTimeoutSeconds
$codebuddyResult.Output | Set-Content -Path $codebuddyLogPath -Encoding UTF8
Assert-ExternalResult -Result $codebuddyResult -StepName "CodeBuddy implementation"

$probeFile = Join-Path $worktreeRoot "CODEBUDDY_PROBE_RESULT.md"
if (-not (Test-Path $probeFile)) {
    throw "CodeBuddy did not create CODEBUDDY_PROBE_RESULT.md"
}

$probeText = Get-Content $probeFile -Raw -Encoding UTF8
if ($probeText -notmatch "probe:\s*ok") {
    throw "CODEBUDDY_PROBE_RESULT.md does not contain required marker: probe: ok"
}

$changedFiles = git -C $worktreeRoot status --short
$forbiddenChanges = $changedFiles | Where-Object { $_ -match "^\s*(M|A|\?\?)\s+(src/|tests/|docs/)" }
if ($forbiddenChanges) {
    throw "CodeBuddy modified forbidden paths:`n$($forbiddenChanges | Out-String)"
}

$codexReviewPrompt = @(
    "You are Codex acting as a read-only quality reviewer. Review the current worktree diff relative to HEAD.",
    "",
    "Rules:",
    "- Do not modify any files.",
    "- Output the content of REVIEW.md.",
    "- Include: conclusion, critical issues, important issues, suggestions, and final pass/fail.",
    "- If the only change is CODEBUDDY_PROBE_RESULT.md and it contains probe: ok, mark it as passed.",
    "- If src/, tests/, or docs/ were modified, mark it as failed."
) -join [Environment]::NewLine

$codexReviewArgs = @(
    "exec",
    "--sandbox", "read-only",
    "--color", "never",
    "--cd", $worktreeRoot,
    $codexReviewPrompt
)
$reviewResult = Invoke-ExternalWithExitEvent `
    -FilePath $codex `
    -CommandArguments $codexReviewArgs `
    -WorkingDirectory $worktreeRoot `
    -TimeoutSeconds $CodexTimeoutSeconds
$reviewResult.Output | Set-Content -Path $reviewPath -Encoding UTF8
Assert-ExternalResult -Result $reviewResult -StepName "Codex review"

$summary = @(
    "# Orchestrator Probe Summary",
    "",
    "- passed: true",
    "- run_id: $runId",
    "- branch: $branchName",
    "- worktree: $worktreeRoot",
    "- run_dir: $runDir",
    "- codebuddy_model: $Model",
    "",
    "## Generated files",
    "",
    "- REQ: $reqPath",
    "- PLAN: $planPath",
    "- CODEBUDDY_LOG: $codebuddyLogPath",
    "- REVIEW: $reviewPath",
    "",
    "## Worktree git status",
    "",
    '````text',
    ($changedFiles | Out-String),
    '````',
    "",
    "## Cleanup",
    "",
    "If you no longer need the probe worktree:",
    "",
    '````powershell',
    "git -C `"$ProjectRoot`" worktree remove `"$worktreeRoot`"",
    "git -C `"$ProjectRoot`" branch -D `"$branchName`"",
    '````'
) -join [Environment]::NewLine

$summary | Set-Content -Path $summaryPath -Encoding UTF8
Write-Host $summary

exit 0
