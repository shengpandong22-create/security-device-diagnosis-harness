<# Offline parser probe. Uses synthetic Codex JSONL and never starts Codex. #>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_codex_usage.ps1")

$jsonl = @'
{"type":"thread.started","thread_id":"synthetic-session"}
{"type":"item.completed","item":{"type":"agent_message","text":"intermediate"}}
{"type":"item.completed","item":{"type":"agent_message","text":"REVIEW_PASSED"}}
{"type":"turn.completed","usage":{"input_tokens":101,"cached_input_tokens":80,"output_tokens":12,"reasoning_output_tokens":3}}
'@
$parsed = ConvertFrom-CodexJsonLines $jsonl
$temp = Join-Path ([IO.Path]::GetTempPath()) ("codex-usage-" + [guid]::NewGuid().ToString("N") + ".jsonl")
try {
    Add-CodexUsageRecord $temp "probe-task" "synthetic_event" "review" $parsed
    $record = Get-Content $temp -Raw | ConvertFrom-Json
    $malformed = ConvertFrom-CodexJsonLines ($jsonl + "`nnot-json")
    $missing = ConvertFrom-CodexJsonLines '{"type":"thread.started","thread_id":"only-thread"}'
    $result = [ordered]@{
        executed_real_models = $false
        complete_stream_accepted = $parsed.Complete
        final_agent_message_selected = $parsed.FinalMessage -eq "REVIEW_PASSED"
        exact_usage_preserved = ($record.input_tokens -eq 101 -and $record.cached_input_tokens -eq 80 -and $record.output_tokens -eq 12 -and $record.reasoning_output_tokens -eq 3)
        identifiers_preserved = ($record.task_id -eq "probe-task" -and $record.session_id -eq "synthetic-session")
        malformed_line_counted = $malformed.ParseErrors -eq 1
        incomplete_stream_rejected = -not $missing.Complete
    }
    $result | ConvertTo-Json
    if (-not $parsed.Complete -or $parsed.FinalMessage -ne "REVIEW_PASSED" -or
        $record.input_tokens -ne 101 -or $record.cached_input_tokens -ne 80 -or
        $record.output_tokens -ne 12 -or $record.reasoning_output_tokens -ne 3 -or
        $malformed.ParseErrors -ne 1 -or $missing.Complete) { exit 1 }
} finally {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
}
