<# Parse Codex JSONL and persist one exact usage record per CLI call. #>

function ConvertFrom-CodexJsonLines {
    param([AllowEmptyString()][string]$JsonLines)

    $threadId = $null
    $finalMessage = $null
    $usage = $null
    $parseErrors = 0
    foreach ($line in ($JsonLines -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $item = $line | ConvertFrom-Json } catch { $parseErrors += 1; continue }
        if ($item.type -eq "thread.started") { $threadId = [string]$item.thread_id }
        if ($item.type -eq "turn.completed") { $usage = $item.usage }
        if ($item.type -eq "item.completed" -and $item.item.type -eq "agent_message") {
            $finalMessage = [string]$item.item.text
        }
    }
    return [pscustomobject]@{
        ThreadId = $threadId
        FinalMessage = $finalMessage
        Usage = $usage
        ParseErrors = $parseErrors
        Complete = ($null -ne $threadId -and $null -ne $finalMessage -and $null -ne $usage)
    }
}

function Add-CodexUsageRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$TaskId,
        [Parameter(Mandatory = $true)][string]$Trigger,
        [Parameter(Mandatory = $true)][string]$Purpose,
        [Parameter(Mandatory = $true)]$Parsed
    )

    $parent = Split-Path $Path -Parent
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $usage = $Parsed.Usage
    [ordered]@{
        timestamp = [DateTimeOffset]::UtcNow.ToString("o")
        task_id = $TaskId
        trigger = $Trigger
        purpose = $Purpose
        session_id = $Parsed.ThreadId
        input_tokens = if ($usage) { [int64]$usage.input_tokens } else { $null }
        cached_input_tokens = if ($usage) { [int64]$usage.cached_input_tokens } else { $null }
        output_tokens = if ($usage) { [int64]$usage.output_tokens } else { $null }
        reasoning_output_tokens = if ($usage) { [int64]$usage.reasoning_output_tokens } else { $null }
        usage_available = [bool]$usage
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-MeasuredCodex {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$CommandArguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$RawLogPath,
        [Parameter(Mandatory = $true)][string]$UsageLogPath,
        [Parameter(Mandatory = $true)][string]$TaskId,
        [Parameter(Mandatory = $true)][string]$Purpose,
        [string]$Trigger = "orchestrator_event"
    )

    if ($CommandArguments -notcontains "--json") { throw "Measured Codex calls must include --json." }
    $result = Invoke-ExternalWithExitEvent -FilePath $FilePath `
        -CommandArguments $CommandArguments -WorkingDirectory $WorkingDirectory `
        -TimeoutSeconds $TimeoutSeconds
    Write-TextFile -Path $RawLogPath -Text $result.Output
    $parsed = ConvertFrom-CodexJsonLines $result.Output
    Add-CodexUsageRecord -Path $UsageLogPath -TaskId $TaskId -Trigger $Trigger `
        -Purpose $Purpose -Parsed $parsed
    return [pscustomobject]@{
        Output = if ($parsed.FinalMessage) { $parsed.FinalMessage } else { $result.Output }
        RawOutput = $result.Output
        ExitCode = $result.ExitCode
        TimedOut = $result.TimedOut
        CompletionSignal = $result.CompletionSignal
        TerminalState = $result.TerminalState
        ThreadId = $parsed.ThreadId
        Usage = $parsed.Usage
        UsageAvailable = [bool]$parsed.Usage
        JsonComplete = $parsed.Complete
        ParseErrors = $parsed.ParseErrors
    }
}
