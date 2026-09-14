<# Offline Phase 1A probe. Uses only local PowerShell child processes. #>

param([string]$OutputRoot = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_process.ps1")

if (-not $OutputRoot) {
    $OutputRoot = Join-Path ([IO.Path]::GetTempPath()) "agent-collab-monitor-probe"
}
Remove-Item -LiteralPath $OutputRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$shell = (Get-Command powershell).Source

function Invoke-ProbeCase {
    param([string]$Name, [string]$Command, [int]$Timeout = 10, [int]$Idle = 0)
    $dir = Join-Path $OutputRoot $Name
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $events = Join-Path $dir "EVENTS.jsonl"
    $heartbeat = Join-Path $dir "HEARTBEAT.json"
    $activity = Join-Path $dir "activity.log"
    $result = Invoke-ExternalWithExitEvent `
        -FilePath $shell `
        -CommandArguments @("-NoProfile", "-Command", $Command) `
        -WorkingDirectory $dir `
        -TimeoutSeconds $Timeout `
        -EventLogPath $events `
        -HeartbeatPath $heartbeat `
        -ActivityPath $activity `
        -IdleTimeoutSeconds $Idle `
        -CheckIntervalSeconds 1
    $rows = @(Get-Content -LiteralPath $events | ForEach-Object { $_ | ConvertFrom-Json })
    $terminal = @($rows | Where-Object codex_wakeup)
    if (@($rows | Where-Object { $_.current_state -eq "running" -and $_.codex_wakeup }).Count -ne 0) {
        throw "$Name woke Codex while running"
    }
    if ($terminal.Count -ne 1) { throw "$Name emitted $($terminal.Count) terminal events" }
    [ordered]@{
        name = $Name
        terminal_state = $terminal[0].current_state
        exit_code = $result.ExitCode
        running_wakeups = 0
        terminal_wakeups = $terminal.Count
        heartbeat_written = Test-Path -LiteralPath $heartbeat
    }
}

$results = @(
    Invoke-ProbeCase "completed" "Start-Sleep 2; exit 0"
    Invoke-ProbeCase "failed" "Start-Sleep 2; exit 7"
    Invoke-ProbeCase "max-turns" "[Console]::Error.WriteLine('Max turns (4) exceeded'); exit 1"
    Invoke-ProbeCase "log-growth" '1..3 | ForEach-Object { Add-Content activity.log $_; Start-Sleep 1 }; exit 0' -Idle 5
    Invoke-ProbeCase "stalled" "Start-Sleep 10" -Timeout 10 -Idle 2
    Invoke-ProbeCase "timeout" "Start-Sleep 10" -Timeout 2
)

$expected = @("completed", "failed", "max_turns_exceeded", "completed", "stalled", "timeout")
for ($index = 0; $index -lt $results.Count; $index++) {
    if ($results[$index].terminal_state -ne $expected[$index]) {
        throw "$($results[$index].name) expected $($expected[$index]), got $($results[$index].terminal_state)"
    }
}

$summary = [ordered]@{
    passed = $true
    real_codebuddy_called = $false
    real_codex_called = $false
    results = $results
}
$summary | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $OutputRoot "SUMMARY.json") -Encoding UTF8
$summary | ConvertTo-Json -Depth 5
