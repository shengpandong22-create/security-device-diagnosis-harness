<#
.SYNOPSIS
Verify whether CodeBuddy CLI can run headlessly, edit files, execute commands, and exit.

.DESCRIPTION
This script is intentionally isolated from the project source tree. It creates a timestamped
probe directory under D:\AgentStudy\codebuddy-cli-probe-runs by default, initializes a git repo,
asks CodeBuddy to create and run a tiny Python file, then validates the real filesystem result.

It treats common false-success outputs such as 429/rate-limit/authentication prompts as failures,
because some CLI failures may still return exit code 0.
#>

param(
    [string]$ProbeRoot = "D:\AgentStudy\codebuddy-cli-probe-runs",
    [string]$Model = "fast-model",
    [int]$MaxTurns = 5,
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-CodeBuddy {
    $command = Get-Command codebuddy -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidate = Join-Path $env:LOCALAPPDATA "codebuddy\bin\codebuddy.exe"
    if (Test-Path $candidate) {
        return $candidate
    }

    throw "CodeBuddy CLI not found. Install it first: irm https://www.codebuddy.cn/cli/install.ps1 | iex"
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
        "Max turns exceeded"
    )

    foreach ($pattern in $patterns) {
        if ($Text -match [regex]::Escape($pattern)) {
            return $pattern
        }
    }
    return $null
}

$codebuddy = Resolve-CodeBuddy
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$probeDir = Join-Path $ProbeRoot "verify-$runId"
New-Item -ItemType Directory -Force -Path $probeDir | Out-Null

git -C $probeDir init | Out-Null

$prompt = @(
    "Headless capability probe.",
    "Do exactly three things:",
    "1. Create probe_hello.py with exactly this content: print('ok')",
    "2. Run python probe_hello.py",
    "3. Do not commit git and do not access network.",
    "Finish with a short ASCII-only English summary."
) -join [Environment]::NewLine

$codeBuddyArguments = @(
    "-p",
    "-y",
    "--model", $Model,
    "--allowedTools", "Read,Write,Edit,Bash",
    "--max-turns", "$MaxTurns",
    "--output-format", "text",
    $prompt
)

$result = Invoke-ExternalWithTimeout `
    -FilePath $codebuddy `
    -CommandArguments $codeBuddyArguments `
    -WorkingDirectory $probeDir `
    -TimeoutSeconds $TimeoutSeconds

$outputPath = Join-Path $probeDir "codebuddy.out.txt"
$result.Output | Set-Content -Path $outputPath -Encoding UTF8

$probeFile = Join-Path $probeDir "probe_hello.py"
$probeFileExists = Test-Path $probeFile
$probeContent = if ($probeFileExists) { Get-Content $probeFile -Raw -Encoding UTF8 } else { "" }
$gitStatus = git -C $probeDir status --short
$falseSuccess = Test-FalseSuccessOutput $result.Output
$falseSuccessText = if ($null -eq $falseSuccess) { "(none)" } else { $falseSuccess }

$passed = (
    -not $result.TimedOut -and
    $result.ExitCode -eq 0 -and
    $probeFileExists -and
    $probeContent.Trim() -eq "print('ok')" -and
    $null -eq $falseSuccess
)

$report = @(
    "# CodeBuddy CLI Headless Verification Report",
    "",
    "- passed: $($passed.ToString().ToLowerInvariant())",
    "- codebuddy: $codebuddy",
    "- model: $Model",
    "- max_turns: $MaxTurns",
    "- timeout_seconds: $TimeoutSeconds",
    "- probe_dir: $probeDir",
    "- exit_code: $($result.ExitCode)",
    "- timed_out: $($result.TimedOut.ToString().ToLowerInvariant())",
    "- false_success_pattern: $falseSuccessText",
    "- probe_file_exists: $($probeFileExists.ToString().ToLowerInvariant())",
    "- probe_file_content: $($probeContent.Trim())",
    "",
    "## git status",
    "",
    '````text',
    ($gitStatus | Out-String),
    '````',
    "",
    "## CodeBuddy output",
    "",
    '````text',
    $result.Output.Trim(),
    '````'
) -join [Environment]::NewLine

$reportPath = Join-Path $probeDir "VERIFY_CODEBUDDY_CLI.md"
$report | Set-Content -Path $reportPath -Encoding UTF8

Write-Host $report

if (-not $passed) {
    exit 1
}

exit 0
