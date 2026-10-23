function Get-TrackedWorktreeSnapshot {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $Names = @(& git -C $RepositoryRoot diff --name-only HEAD --)
    if ($LASTEXITCODE -ne 0) { throw "无法读取受跟踪文件差异名单" }
    $Diff = (& git -C $RepositoryRoot diff --binary HEAD -- | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "无法读取受跟踪文件差异内容" }
    $Bytes = [Text.Encoding]::UTF8.GetBytes($Diff)
    $Hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes))
    [PSCustomObject]@{
        Names = @($Names | Sort-Object)
        DiffHash = $Hash
    }
}

function Assert-TrackedWorktreeUnchanged {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After
    )

    $NamesUnchanged = (@($Before.Names) -join "`n") -ceq (@($After.Names) -join "`n")
    if (-not $NamesUnchanged -or $Before.DiffHash -cne $After.DiffHash) {
        throw "验证过程改变了受跟踪工作树；请检查验证命令的写入行为"
    }
}
