<#
.SYNOPSIS
Run a headless CLI and wait for its operating-system exit event.

.DESCRIPTION
This shared primitive does not poll either model. Completion is signaled by
System.Diagnostics.Process.Exited; exit code and handoff files remain separate gates.
#>

function Invoke-ExternalWithExitEvent {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$CommandArguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [string]$EventLogPath = "",
        [string]$HeartbeatPath = "",
        [string]$ActivityPath = "",
        [int]$IdleTimeoutSeconds = 0,
        [int]$CheckIntervalSeconds = 2,
        [string[]]$ErrorPatterns = @("Max turns", "429", "rate limit", "permission prompts")
    )

    function Write-TransitionEvent {
        param([string]$Previous, [string]$Current, [bool]$WakeCodex, [int]$ExitCode)
        if (-not $EventLogPath) { return }
        $parent = Split-Path $EventLogPath -Parent
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        [ordered]@{
            timestamp = [DateTimeOffset]::UtcNow.ToString("o")
            event = $Current
            previous_state = $Previous
            current_state = $Current
            process_id = if ($process.Id) { $process.Id } else { $null }
            exit_code = if ($ExitCode -ge 0) { $ExitCode } else { $null }
            codex_wakeup = $WakeCodex
        } | ConvertTo-Json -Compress | Add-Content -LiteralPath $EventLogPath -Encoding UTF8
    }

    function Get-ActivityStamp {
        if (-not $ActivityPath -or -not (Test-Path -LiteralPath $ActivityPath)) { return $null }
        $item = Get-Item -LiteralPath $ActivityPath
        if (-not $item.PSIsContainer) {
            return "$($item.Length):$($item.LastWriteTimeUtc.Ticks)"
        }
        $latest = Get-ChildItem -LiteralPath $ActivityPath -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if ($null -eq $latest) { return "empty" }
        return "$($latest.Length):$($latest.LastWriteTimeUtc.Ticks)"
    }

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    if ($startInfo.PSObject.Properties.Name -contains "ArgumentList") {
        foreach ($argument in $CommandArguments) {
            [void]$startInfo.ArgumentList.Add($argument)
        }
    }
    else {
        # Windows PowerShell 5.1 / .NET Framework has no ArgumentList API.
        # Quote every argument and escape quote-adjacent/trailing backslashes
        # according to CommandLineToArgvW rules.
        $quotedArguments = foreach ($argument in $CommandArguments) {
            $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
            $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
            '"' + $escaped + '"'
        }
        $startInfo.Arguments = $quotedArguments -join " "
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $process.EnableRaisingEvents = $true
    $sourceId = "agent-collab-process-$([guid]::NewGuid().ToString('N'))"
    $eventParameters = @{
        InputObject = $process
        EventName = "Exited"
        SourceIdentifier = $sourceId
    }
    $subscription = Register-ObjectEvent @eventParameters

    try {
        if (-not $process.Start()) {
            throw "Failed to start process: $FilePath"
        }
        Write-TransitionEvent "created" "running" $false -1
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $startedAt = [DateTimeOffset]::UtcNow
        $lastActivityAt = $startedAt
        $lastActivityStamp = Get-ActivityStamp
        $terminalSignal = $null
        while (-not $process.HasExited) {
            $elapsed = ([DateTimeOffset]::UtcNow - $startedAt).TotalSeconds
            if ($elapsed -ge $TimeoutSeconds) { $terminalSignal = "timeout"; break }
            $event = Wait-Event -SourceIdentifier $sourceId -Timeout $CheckIntervalSeconds
            if ($null -ne $event) { break }
            $stamp = Get-ActivityStamp
            if ($null -ne $stamp -and $stamp -ne $lastActivityStamp) {
                $lastActivityStamp = $stamp
                $lastActivityAt = [DateTimeOffset]::UtcNow
            }
            if ($HeartbeatPath) {
                [ordered]@{
                    timestamp = [DateTimeOffset]::UtcNow.ToString("o")
                    process_id = $process.Id
                    state = "running"
                    activity_stamp = $lastActivityStamp
                } | ConvertTo-Json -Compress | Set-Content -LiteralPath $HeartbeatPath -Encoding UTF8
            }
            if ($IdleTimeoutSeconds -gt 0 -and (
                [DateTimeOffset]::UtcNow - $lastActivityAt
            ).TotalSeconds -ge $IdleTimeoutSeconds) {
                $terminalSignal = "stalled"
                break
            }
        }
        if ($terminalSignal) {
            try { $process.Kill($true) } catch { }
            $process.WaitForExit()
            $code = if ($terminalSignal -eq "timeout") { 124 } else { 125 }
            Write-TransitionEvent "running" $terminalSignal $true $code
            return [pscustomobject]@{
                Output = "$($terminalSignal.ToUpperInvariant()) after $([math]::Round(([DateTimeOffset]::UtcNow - $startedAt).TotalSeconds, 3)) seconds"
                ExitCode = $code
                TimedOut = $terminalSignal -eq "timeout"
                CompletionSignal = $terminalSignal
                TerminalState = $terminalSignal
            }
        }

        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $combined = @($stdout.TrimEnd(), $stderr.TrimEnd()) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        $signal = if ($process.ExitCode -eq 0) { "completed" } else { "failed" }
        foreach ($pattern in $ErrorPatterns) {
            if (($combined -join [Environment]::NewLine) -match [regex]::Escape($pattern)) {
                $signal = if ($pattern -match "Max turns") { "max_turns_exceeded" } else { "failed" }
                break
            }
        }
        Write-TransitionEvent "running" $signal $true $process.ExitCode
        return [pscustomobject]@{
            Output = ($combined -join [Environment]::NewLine)
            ExitCode = $process.ExitCode
            TimedOut = $false
            CompletionSignal = "process_exited_event"
            TerminalState = $signal
        }
    }
    finally {
        Remove-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
        Unregister-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
        $subscription | Remove-Job -Force -ErrorAction SilentlyContinue
        $process.Dispose()
    }
}
