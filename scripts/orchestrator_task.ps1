<#
.SYNOPSIS
Run a bounded Codex -> CodeBuddy -> Codex task handoff.

.DESCRIPTION
This script is the practical version of orchestrator_probe.ps1.

It accepts a requirement file, creates an isolated git worktree, optionally asks
Codex CLI to produce a plan, lets CodeBuddy implement the task, optionally asks
Codex CLI to review the diff, and stops for human inspection. It never merges
or pushes automatically. Requirement files may declare `task_size: probe|small|medium`;
the default turn budgets are 8/24/36. A max-turn stop may continue once in the
same worktree, within the original total timeout, before any Codex review.

Recommended first run:

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\orchestrator_task.ps1 `
  -ReqFile .agent-collab\inbox\phase3a-1-access-domain.md `
  -UseReqAsPlanOnCodexFailure `
  -AllowDirty
#>

param(
    [Parameter(Mandatory = $true)][string]$ReqFile,
    [string]$ProjectRoot = "",
    [string]$TaskName = "",
    [ValidateSet("", "probe", "small", "medium")][string]$TaskSize = "",
    [string]$CodeBuddyModel = "fast-model",
    [string]$CodexModel = "gpt-5.6-luna",
    [int]$CodeBuddyMaxTurns = 0,
    [int]$MaxContinuationRounds = 1,
    [int]$MaxFixRounds = 1,
    [int]$CodexTimeoutSeconds = 300,
    [int]$CodeBuddyTimeoutSeconds = 1800,
    [switch]$UseReqAsPlanOnCodexFailure,
    [switch]$SkipCodexPlan,
    [switch]$SkipCodexReview,
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

function Test-ExternalResultOk {
    param([Parameter(Mandatory = $true)]$Result)

    $falseSuccess = Test-FalseSuccessOutput $Result.Output
    return (-not $Result.TimedOut -and $Result.ExitCode -eq 0 -and $null -eq $falseSuccess)
}

function Test-ReviewResultOk {
    param([Parameter(Mandatory = $true)]$Result)

    if ($Result.TimedOut -or $Result.ExitCode -ne 0) {
        return $false
    }
    $passedAt = $Result.Output.LastIndexOf("REVIEW_PASSED", [System.StringComparison]::Ordinal)
    $failedAt = $Result.Output.LastIndexOf("REVIEW_FAILED", [System.StringComparison]::Ordinal)
    return ($passedAt -ge 0 -and $passedAt -gt $failedAt)
}

function Test-MaxTurnsOutput {
    param([string]$Text)

    return $Text -match "Max turns(?:\s+\(\d+\))?\s+exceeded"
}

function Resolve-TaskSize {
    param(
        [string]$ExplicitSize,
        [string]$RequirementText
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitSize)) {
        return $ExplicitSize.ToLowerInvariant()
    }
    if ($RequirementText -match "(?im)^\s*task_size\s*:\s*(probe|small|medium)\s*$") {
        return $Matches[1].ToLowerInvariant()
    }
    return "small"
}

function Resolve-MaxTurns {
    param(
        [string]$Size,
        [int]$ExplicitMaxTurns
    )

    if ($ExplicitMaxTurns -gt 0) {
        return $ExplicitMaxTurns
    }
    return @{ probe = 8; small = 24; medium = 36 }[$Size]
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    Write-TextFile -Path $Path -Text ($Value | ConvertTo-Json -Depth 8)
}

function Convert-ToSafeSlug {
    param([string]$Text)

    $slug = $Text.ToLowerInvariant() -replace "[^a-z0-9._-]+", "-"
    $slug = $slug.Trim("-")
    if ([string]::IsNullOrWhiteSpace($slug)) {
        return "task"
    }
    return $slug
}

function Write-TextFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $parent = Split-Path $Path -Parent
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Text | Set-Content -Path $Path -Encoding UTF8
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}
$ReqFile = (Resolve-Path $ReqFile).Path
$reqName = [System.IO.Path]::GetFileNameWithoutExtension($ReqFile)
if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $TaskName = $reqName
}
$taskSlug = Convert-ToSafeSlug $TaskName

$status = git -C $ProjectRoot status --short
if ($status -and -not $AllowDirty) {
    throw "Project worktree is not clean. Commit/stash changes first, or pass -AllowDirty. Current status:`n$status"
}

$codebuddy = Resolve-CommandPath `
    -Name "codebuddy" `
    -Fallback (Join-Path $env:LOCALAPPDATA "codebuddy\bin\codebuddy.exe")
$codex = $null
if (-not ($SkipCodexPlan -and $SkipCodexReview)) {
    $codex = Resolve-CommandPath -Name "codex"
}

$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $ProjectRoot ".agent-collab\runs\$taskSlug-$runId"
$worktreeRoot = Join-Path (Split-Path $ProjectRoot -Parent) "security-device-diagnosis-harness-$taskSlug-$runId"
$branchName = "codex/$taskSlug-$runId"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$planPath = Join-Path $runDir "PLAN.md"
$planLogPath = Join-Path $runDir "CODEX_PLAN.log.txt"
$codebuddyLogPath = Join-Path $runDir "CODEBUDDY_IMPL.log.txt"
$reviewPath = Join-Path $runDir "REVIEW.md"
$reviewLogPath = Join-Path $runDir "CODEX_REVIEW.log.txt"
$summaryPath = Join-Path $runDir "SUMMARY.md"
$continuationPath = Join-Path $runDir "NEEDS_CONTINUATION.json"

$reqText = Get-Content $ReqFile -Raw -Encoding UTF8
$resolvedTaskSize = Resolve-TaskSize -ExplicitSize $TaskSize -RequirementText $reqText
$resolvedMaxTurns = Resolve-MaxTurns -Size $resolvedTaskSize -ExplicitMaxTurns $CodeBuddyMaxTurns
if ($MaxContinuationRounds -lt 0 -or $MaxContinuationRounds -gt 1) {
    throw "MaxContinuationRounds must be 0 or 1."
}

if ($SkipCodexPlan) {
    Write-TextFile -Path $planPath -Text $reqText
}
else {
    $codexPlanPrompt = @(
        "You are Codex acting as designer and quality supervisor.",
        "Read the requirement and produce a bounded implementation plan for CodeBuddy.",
        "",
        "Rules:",
        "- Do not modify files.",
        "- Keep the task scope minimal.",
        "- Include acceptance criteria, forbidden changes, and validation commands.",
        "- Output Markdown only.",
        "",
        "Requirement file: $ReqFile"
    ) -join [Environment]::NewLine

    $codexPlanArgs = @(
        "exec",
        "--model", $CodexModel,
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
    Write-TextFile -Path $planLogPath -Text $planResult.Output

    if (Test-ExternalResultOk $planResult) {
        Write-TextFile -Path $planPath -Text $planResult.Output
    }
    elseif ($UseReqAsPlanOnCodexFailure) {
        $fallbackPlan = @(
            "# Fallback Plan",
            "",
            "Codex CLI did not produce a plan within the configured timeout.",
            "The requirement file is used as the authoritative plan for this bounded run.",
            "",
            "## Original Requirement",
            "",
            $reqText
        ) -join [Environment]::NewLine
        Write-TextFile -Path $planPath -Text $fallbackPlan
    }
    else {
        throw "Codex plan failed. See $planLogPath"
    }
}

git -C $ProjectRoot worktree add -b $branchName $worktreeRoot HEAD | Out-Null

$codebuddyPrompt = @(
    "You are CodeBuddy acting as implementation engineer.",
    "",
    "Implement the task in this isolated git worktree:",
    $worktreeRoot,
    "",
    "Authoritative requirement file:",
    $ReqFile,
    "",
    "Implementation plan file:",
    $planPath,
    "",
    "Hard rules:",
    "- Follow the requirement scope exactly.",
    "- Do not implement future phases.",
    "- Do not read or write .env files.",
    "- Do not use real API keys, tokens, credentials, real devices, or biometric data.",
    "- Do not push, merge, or create pull requests.",
    "- Run the validation commands required by the plan.",
    "- Commit your changes locally using small English conventional commits.",
    "",
    "Final report:",
    "- Print changed files.",
    "- Print validation commands and results.",
    "- Print git log --oneline -8.",
    "- Print git status --short.",
    "- Explain any deviation from the requirement."
) -join [Environment]::NewLine

$codebuddyArgs = @(
    "-p",
    "-y",
    "--model", $CodeBuddyModel,
    "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
    "--max-turns", "$resolvedMaxTurns",
    "--output-format", "text",
    $codebuddyPrompt
)
$codeBuddyStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$implementationResult = Invoke-ExternalWithExitEvent `
    -FilePath $codebuddy `
    -CommandArguments $codebuddyArgs `
    -WorkingDirectory $worktreeRoot `
    -TimeoutSeconds $CodeBuddyTimeoutSeconds
Write-TextFile -Path $codebuddyLogPath -Text $implementationResult.Output

$implOk = Test-ExternalResultOk $implementationResult
$continuationRoundsUsed = 0
$implementationAttempts = 1

if (-not $implOk -and (Test-MaxTurnsOutput $implementationResult.Output)) {
    $continuationState = [ordered]@{
        schema_version = 1
        status = "needs_continuation"
        task = $TaskName
        task_size = $resolvedTaskSize
        worktree = $worktreeRoot
        branch = $branchName
        reason = "max_turns_exceeded"
        initial_max_turns = $resolvedMaxTurns
        continuation_rounds_allowed = $MaxContinuationRounds
        continuation_rounds_used = 0
        elapsed_seconds = [math]::Round($codeBuddyStopwatch.Elapsed.TotalSeconds, 3)
        git_status = @(git -C $worktreeRoot status --short)
    }
    Write-JsonFile -Path $continuationPath -Value $continuationState

    if ($MaxContinuationRounds -eq 1) {
        $remainingSeconds = $CodeBuddyTimeoutSeconds - [int][math]::Ceiling($codeBuddyStopwatch.Elapsed.TotalSeconds)
        if ($remainingSeconds -gt 0) {
            $continuationRoundsUsed = 1
            $implementationAttempts = 2
            $continuationPrompt = @(
                "Continue the same bounded task in the existing isolated worktree.",
                "Do not restart or discard completed work.",
                "Inspect git status and the previous implementation before acting.",
                "Finish only the remaining requirement, run the required validation, and commit locally.",
                "Do not push, merge, access .env, real credentials, real devices, or external services.",
                "Requirement file: $ReqFile",
                "Plan file: $planPath",
                "Previous attempt log: $codebuddyLogPath"
            ) -join [Environment]::NewLine
            $continuationArgs = @(
                "-p", "-y",
                "--model", $CodeBuddyModel,
                "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
                "--max-turns", "$resolvedMaxTurns",
                "--output-format", "text",
                $continuationPrompt
            )
            $continuationResult = Invoke-ExternalWithExitEvent `
                -FilePath $codebuddy `
                -CommandArguments $continuationArgs `
                -WorkingDirectory $worktreeRoot `
                -TimeoutSeconds $remainingSeconds
            $combinedImplementationLog = @(
                $implementationResult.Output,
                "`n===== CONTINUATION 1 =====`n",
                $continuationResult.Output
            ) -join [Environment]::NewLine
            Write-TextFile -Path $codebuddyLogPath -Text $combinedImplementationLog
            $implementationResult = $continuationResult
            $implOk = Test-ExternalResultOk $continuationResult
            $continuationState.status = if ($implOk) { "continued_successfully" } else { "continuation_failed" }
            $continuationState.continuation_rounds_used = 1
            $continuationState.elapsed_seconds = [math]::Round($codeBuddyStopwatch.Elapsed.TotalSeconds, 3)
            $continuationState.git_status = @(git -C $worktreeRoot status --short)
            Write-JsonFile -Path $continuationPath -Value $continuationState
        }
    }
}
$codeBuddyStopwatch.Stop()

$reviewOk = $false
if ($implOk -and -not $SkipCodexReview) {
    $reviewPrompt = @(
        "You are Codex acting as a strict read-only reviewer.",
        "Review the current branch relative to HEAD~1 or main if available.",
        "",
        "Rules:",
        "- Do not modify files.",
        "- Check scope, safety boundaries, tests, docs, and git status.",
        "- Output Markdown only.",
        "- Include sections: conclusion, critical issues, important issues, suggestions, validation verdict.",
        "- If there are critical issues, say REVIEW_FAILED.",
        "- If there are no critical issues, say REVIEW_PASSED.",
        "",
        "Requirement file: $ReqFile",
        "Plan file: $planPath"
    ) -join [Environment]::NewLine

    $reviewArgs = @(
        "exec",
        "--model", $CodexModel,
        "--sandbox", "read-only",
        "--color", "never",
        "--cd", $worktreeRoot,
        $reviewPrompt
    )
    $reviewResult = Invoke-ExternalWithExitEvent `
        -FilePath $codex `
        -CommandArguments $reviewArgs `
        -WorkingDirectory $worktreeRoot `
        -TimeoutSeconds $CodexTimeoutSeconds
    Write-TextFile -Path $reviewLogPath -Text $reviewResult.Output
    Write-TextFile -Path $reviewPath -Text $reviewResult.Output
    $reviewOk = Test-ReviewResultOk $reviewResult
}
elseif ($implOk -and $SkipCodexReview) {
    Write-TextFile -Path $reviewPath -Text "# Review skipped`n`nSkipCodexReview was set for this run."
    $reviewOk = $true
}

$changedFiles = git -C $worktreeRoot status --short
$headLog = git -C $worktreeRoot log --oneline -8
$passed = ($implOk -and ($SkipCodexReview -or $reviewOk))

$summary = @(
    "# Orchestrator Task Summary",
    "",
    "- passed: $($passed.ToString().ToLowerInvariant())",
    "- task: $TaskName",
    "- run_id: $runId",
    "- branch: $branchName",
    "- worktree: $worktreeRoot",
    "- run_dir: $runDir",
    "- codebuddy_model: $CodeBuddyModel",
    "- task_size: $resolvedTaskSize",
    "- codebuddy_max_turns_per_attempt: $resolvedMaxTurns",
    "- implementation_attempts: $implementationAttempts",
    "- continuation_rounds_used: $continuationRoundsUsed",
    "- codebuddy_elapsed_seconds: $([math]::Round($codeBuddyStopwatch.Elapsed.TotalSeconds, 3))",
    "- codex_model: $CodexModel",
    "- implementation_ok: $($implOk.ToString().ToLowerInvariant())",
    "- review_ok: $($reviewOk.ToString().ToLowerInvariant())",
    "",
    "## Files",
    "",
    "- REQ: $ReqFile",
    "- PLAN: $planPath",
    "- CODEBUDDY_LOG: $codebuddyLogPath",
    "- CONTINUATION_STATE: $continuationPath",
    "- REVIEW: $reviewPath",
    "",
    "## Worktree git status",
    "",
    '````text',
    ($changedFiles | Out-String),
    '````',
    "",
    "## Worktree git log",
    "",
    '````text',
    ($headLog | Out-String),
    '````',
    "",
    "## Cleanup",
    "",
    '````powershell',
    "git -C `"$ProjectRoot`" worktree remove `"$worktreeRoot`"",
    "git -C `"$ProjectRoot`" branch -D `"$branchName`"",
    '````'
) -join [Environment]::NewLine

Write-TextFile -Path $summaryPath -Text $summary
Write-Host $summary

if (-not $passed) {
    exit 1
}

exit 0
