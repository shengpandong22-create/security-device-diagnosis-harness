<# Offline probe for the production orchestrator attestation functions. #>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "collab_process.ps1")
. (Join-Path $PSScriptRoot "collab_protocol.ps1")
. (Join-Path $PSScriptRoot "collab_attestation.ps1")

$root = Join-Path ([IO.Path]::GetTempPath()) ("collab-attestation-" + [guid]::NewGuid().ToString("N"))
$path = "$root.json"
try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    "before" | Set-Content (Join-Path $root "sample.txt") -Encoding UTF8
    git -C $root init -q
    git -C $root config user.name "Attestation Probe"
    git -C $root config user.email "attestation-probe@invalid.local"
    git -C $root add .
    git -C $root commit -q -m "test: create fixture"
    $base = (git -C $root rev-parse HEAD).Trim()
    "after" | Set-Content (Join-Path $root "sample.txt") -Encoding UTF8
    git -C $root add sample.txt
    git -C $root commit -q -m "feat: update fixture"
    $head = (git -C $root rev-parse HEAD).Trim()
    Invoke-HostValidationAttestation -WorktreeRoot $root -BaseCommit $base `
        -HeadCommit $head -Commands @("git diff --check $base...$head") `
        -OutputPath $path -TimeoutSeconds 30 | Out-Null
    $valid = Test-HostValidationAttestation $path $root $base $head
    $stored = Get-Content $path -Raw | ConvertFrom-Json
    $stored.file_sha256.'sample.txt' = "0" * 64
    Write-CollabJson $path $stored
    $tamperRejected = -not (Test-HostValidationAttestation $path $root $base $head)
    $result = [ordered]@{
        executed_real_models = $false
        valid_attestation_accepted = $valid
        tampered_file_hash_rejected = $tamperRejected
        validation_command_count = 1
    }
    $result | ConvertTo-Json
    if (-not $valid -or -not $tamperRejected) { exit 1 }
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}
