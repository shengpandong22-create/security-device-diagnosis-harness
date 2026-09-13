<# Offline probe for the structured collaboration protocol. No model CLI is called. #>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_protocol.ps1")

$root = Join-Path ([System.IO.Path]::GetTempPath()) "agent-collab-protocol-probe"
New-Item -ItemType Directory -Force -Path $root | Out-Null
$handoff = Join-Path $root "HANDOFF.json"
$takeover = Join-Path $root "CODEX_TAKEOVER.json"

Write-CollabJson $handoff ([ordered]@{
    schema_version = 2
    status = "ready_for_review"
    base_commit = "aaaaaaa"
    head_commit = "bbbbbbb"
    worktree_clean = $true
    changed_files = @("src/example.py")
    validation = @([ordered]@{command = "pytest"; exit_code = 0})
    remaining_items = @()
    risks = @()
})
$valid = Test-CodeBuddyHandoff $handoff "aaaaaaa" "bbbbbbb" $true
$offline = Test-OfflineCollabOutput "uv run pytest tests/unit -q"
$blocked = Test-OfflineCollabOutput "git push origin main"
Write-CodexTakeover $takeover "probe" "D:\isolated" "codex/probe" `
    "aaaaaaa" "bbbbbbb" "max_turns_exceeded" 2
$takeoverValue = Get-Content $takeover -Raw -Encoding UTF8 | ConvertFrom-Json

$result = [ordered]@{
    valid_handoff_accepted = $valid.Ok
    offline_validation_accepted = $offline.Ok
    external_action_rejected = -not $blocked.Ok
    takeover_preserves_worktree = $takeoverValue.preserve_worktree
    takeover_restarts_codebuddy = $takeoverValue.restart_codebuddy
}
$result | ConvertTo-Json
if ($result.Values -contains $false -and -not (
    $result.takeover_restarts_codebuddy -eq $false -and
    $result.valid_handoff_accepted -and
    $result.offline_validation_accepted -and
    $result.external_action_rejected -and
    $result.takeover_preserves_worktree
)) { exit 1 }
exit 0
