<# Shared structured handoff protocol for Codex x CodeBuddy orchestration. #>

Set-StrictMode -Version Latest

function Write-CollabJson {
    param([string]$Path, $Value)
    $parent = Split-Path $Path -Parent
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $Value | ConvertTo-Json -Depth 10 | Set-Content -Path $Path -Encoding UTF8
}

function Write-CollabState {
    param(
        [string]$Path,
        [string]$Status,
        [string]$Task,
        [string]$Branch,
        [string]$BaseCommit,
        [string]$HeadCommit = "",
        [string]$Reason = ""
    )
    Write-CollabJson $Path ([ordered]@{
        schema_version = 2
        status = $Status
        task = $Task
        branch = $Branch
        base_commit = $BaseCommit
        head_commit = $HeadCommit
        reason = $Reason
        updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    })
}

function Test-CodeBuddyHandoff {
    param(
        [string]$Path,
        [string]$ExpectedBaseCommit,
        [string]$ExpectedHeadCommit,
        [bool]$ExpectedClean
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ Ok = $false; Reason = "handoff_missing" }
    }
    try { $value = Get-Content $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return [pscustomobject]@{ Ok = $false; Reason = "handoff_invalid_json" } }

    $required = @("schema_version", "status", "base_commit", "head_commit",
        "worktree_clean", "changed_files", "validation", "remaining_items", "risks")
    foreach ($name in $required) {
        if (-not ($value.PSObject.Properties.Name -contains $name)) {
            return [pscustomobject]@{ Ok = $false; Reason = "handoff_missing_$name" }
        }
    }
    if ($value.schema_version -ne 2 -or $value.status -ne "ready_for_review") {
        return [pscustomobject]@{ Ok = $false; Reason = "handoff_not_ready" }
    }
    if ($value.base_commit -ne $ExpectedBaseCommit -or $value.head_commit -ne $ExpectedHeadCommit) {
        return [pscustomobject]@{ Ok = $false; Reason = "handoff_commit_mismatch" }
    }
    if ([bool]$value.worktree_clean -ne $ExpectedClean) {
        return [pscustomobject]@{ Ok = $false; Reason = "handoff_cleanliness_mismatch" }
    }
    if (@($value.remaining_items).Count -gt 0) {
        return [pscustomobject]@{ Ok = $false; Reason = "handoff_has_remaining_items" }
    }
    if (@($value.validation).Count -eq 0) {
        return [pscustomobject]@{ Ok = $false; Reason = "handoff_has_no_validation" }
    }
    foreach ($item in @($value.validation)) {
        if (-not ($item.PSObject.Properties.Name -contains "command") -or
            -not ($item.PSObject.Properties.Name -contains "exit_code") -or
            [int]$item.exit_code -ne 0) {
            return [pscustomobject]@{ Ok = $false; Reason = "handoff_validation_failed" }
        }
        $offline = Test-OfflineCollabOutput ([string]$item.command)
        if (-not $offline.Ok) {
            return [pscustomobject]@{ Ok = $false; Reason = $offline.Reason }
        }
    }
    return [pscustomobject]@{ Ok = $true; Reason = "ready_for_review" }
}

function Test-OfflineCollabOutput {
    param([string]$Text)
    $forbidden = @(
        "--execute-real-model", "eval_phase7_real_model.py",
        "demo_phase5_knowledge_loop.py", "authorized_device_e2e",
        "SECURITY_DIAGNOSIS_EVAL_API_KEY", "git push", "Invoke-WebRequest",
        "curl http", "curl https"
    )
    foreach ($pattern in $forbidden) {
        if ($Text -match [regex]::Escape($pattern)) {
            return [pscustomobject]@{ Ok = $false; Reason = "forbidden_external_action:$pattern" }
        }
    }
    return [pscustomobject]@{ Ok = $true; Reason = "offline_contract_clear" }
}

function Write-CodexTakeover {
    param(
        [string]$Path,
        [string]$Task,
        [string]$Worktree,
        [string]$Branch,
        [string]$BaseCommit,
        [string]$HeadCommit,
        [string]$Reason,
        [int]$ImplementationAttempts
    )
    Write-CollabJson $Path ([ordered]@{
        schema_version = 2
        status = "codex_takeover_required"
        task = $Task
        worktree = $Worktree
        branch = $Branch
        base_commit = $BaseCommit
        head_commit = $HeadCommit
        reason = $Reason
        implementation_attempts = $ImplementationAttempts
        restart_codebuddy = $false
        preserve_worktree = $true
        next_action = "Codex reviews the saved diff and continues from head_commit"
    })
}
