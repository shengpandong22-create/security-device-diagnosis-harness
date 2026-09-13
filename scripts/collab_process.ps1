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
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

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
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $event = Wait-Event -SourceIdentifier $sourceId -Timeout $TimeoutSeconds
        if ($null -eq $event) {
            try { $process.Kill($true) } catch { }
            $process.WaitForExit()
            return [pscustomobject]@{
                Output = "TIMEOUT after $TimeoutSeconds seconds"
                ExitCode = 124
                TimedOut = $true
                CompletionSignal = "timeout"
            }
        }

        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $combined = @($stdout.TrimEnd(), $stderr.TrimEnd()) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        return [pscustomobject]@{
            Output = ($combined -join [Environment]::NewLine)
            ExitCode = $process.ExitCode
            TimedOut = $false
            CompletionSignal = "process_exited_event"
        }
    }
    finally {
        Remove-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
        Unregister-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
        $subscription | Remove-Job -Force -ErrorAction SilentlyContinue
        $process.Dispose()
    }
}
