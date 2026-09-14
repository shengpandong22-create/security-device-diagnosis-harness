<# Deterministic host-validation attestations for Codex/CodeBuddy handoffs. #>

function Get-CollabFileHash {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-CollabTextHash {
    param([AllowEmptyString()][string]$Text)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($algorithm.ComputeHash($bytes)) -replace "-", "").ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function Invoke-HostValidationAttestation {
    param(
        [Parameter(Mandatory = $true)][string]$WorktreeRoot,
        [Parameter(Mandatory = $true)][string]$BaseCommit,
        [Parameter(Mandatory = $true)][string]$HeadCommit,
        [Parameter(Mandatory = $true)][string[]]$Commands,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [int]$TimeoutSeconds = 600
    )

    if ($Commands.Count -eq 0) { throw "At least one trusted host validation command is required." }
    if (@(git -C $WorktreeRoot status --porcelain).Count -ne 0) { throw "Worktree must be clean before host validation." }
    if ((git -C $WorktreeRoot rev-parse HEAD).Trim() -ne $HeadCommit) { throw "Attested HEAD does not match worktree HEAD." }
    $shell = (Get-Command powershell).Source
    $validations = @()
    foreach ($command in $Commands) {
        $result = Invoke-ExternalWithExitEvent -FilePath $shell -CommandArguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command
        ) -WorkingDirectory $WorktreeRoot -TimeoutSeconds $TimeoutSeconds
        $validations += [ordered]@{
            command = $command
            exit_code = $result.ExitCode
            timed_out = $result.TimedOut
            output_sha256 = Get-CollabTextHash $result.Output
        }
        if ($result.TimedOut -or $result.ExitCode -ne 0) {
            throw "Host validation failed: $command"
        }
    }

    $changedFiles = @(git -C $WorktreeRoot diff --name-only "$BaseCommit...$HeadCommit")
    $fileHashes = [ordered]@{}
    foreach ($relativePath in $changedFiles) {
        $path = Join-Path $WorktreeRoot $relativePath
        $fileHashes[$relativePath] = if (Test-Path -LiteralPath $path -PathType Leaf) {
            Get-CollabFileHash $path
        } else { $null }
    }
    $attestation = [ordered]@{
        schema_version = 1
        generated_by = "orchestrator_host"
        base_commit = $BaseCommit
        head_commit = $HeadCommit
        worktree_clean = $true
        changed_files = $changedFiles
        file_sha256 = $fileHashes
        validation = $validations
    }
    Write-CollabJson $OutputPath $attestation
    return $attestation
}

function Test-HostValidationAttestation {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$WorktreeRoot,
        [Parameter(Mandatory = $true)][string]$BaseCommit,
        [Parameter(Mandatory = $true)][string]$HeadCommit
    )

    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try { $item = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $false }
    if ($item.schema_version -ne 1 -or $item.generated_by -ne "orchestrator_host") { return $false }
    if ($item.base_commit -ne $BaseCommit -or $item.head_commit -ne $HeadCommit) { return $false }
    if (-not $item.worktree_clean -or @(git -C $WorktreeRoot status --porcelain).Count -ne 0) { return $false }
    if (@($item.validation).Count -eq 0 -or @($item.validation | Where-Object { $_.exit_code -ne 0 -or $_.timed_out }).Count -ne 0) { return $false }
    $actualFiles = @(git -C $WorktreeRoot diff --name-only "$BaseCommit...$HeadCommit")
    if (($actualFiles -join "`n") -ne (@($item.changed_files) -join "`n")) { return $false }
    foreach ($relativePath in $actualFiles) {
        $candidate = Join-Path $WorktreeRoot $relativePath
        $property = $item.file_sha256.PSObject.Properties[$relativePath]
        if ($null -eq $property) { return $false }
        $expected = $property.Value
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            if ((Get-CollabFileHash $candidate) -ne $expected) { return $false }
        } elseif ($null -ne $expected) { return $false }
    }
    return $true
}
