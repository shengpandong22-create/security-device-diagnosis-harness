param(
    [ValidateSet("Bootstrap", "Start", "Verify", "Status", "Stop")]
    [string]$Command = "Status"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LabRoot = Join-Path $RepoRoot ".device-lab"
$BinRoot = Join-Path $LabRoot "bin"
$RuntimeRoot = Join-Path $LabRoot "runtime"
$OnvifVersion = "0.4.0"
$OnvifSha256 = "a836eea45c6bfb30c21951ce517fd6c26293f8f605898d30b95e102278fb9450"
$OnvifZip = Join-Path $LabRoot "onvif-simulator_$OnvifVersion.zip"
$OnvifExe = Join-Path $BinRoot "onvif-simulator.exe"
$StatePath = Join-Path $RuntimeRoot "state.json"
$CredentialPath = Join-Path $RuntimeRoot "credential.txt"
$ComposeFile = Join-Path $RepoRoot "device-lab/docker-compose.yml"
$OnvifConfig = Join-Path $RepoRoot "device-lab/onvif-simulator.template.json"
$RuntimeOnvifConfig = Join-Path $RuntimeRoot "onvif-simulator.json"
$OnvifCredentialPath = Join-Path $RuntimeRoot "onvif-credential.txt"
$BlackVideoPath = Join-Path $LabRoot "media/black.mp4"
$FfmpegImage = "jrottenberg/ffmpeg:7.1-alpine@sha256:8ec1ee1f6a0fcd37c97725827b6b7832795c9596e3439b8da56d7700d61ae778"

function Initialize-LabDirectories {
    New-Item -ItemType Directory -Force -Path $BinRoot, $RuntimeRoot | Out-Null
}

function Invoke-Bootstrap {
    Initialize-LabDirectories
    if (-not (Test-Path $OnvifExe)) {
        $url = "https://github.com/GyeongHoKim/onvif-simulator/releases/download/v$OnvifVersion/onvif-simulator_${OnvifVersion}_windows_amd64.zip"
        Invoke-WebRequest $url -OutFile $OnvifZip
        $actual = (Get-FileHash $OnvifZip -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $OnvifSha256) {
            throw "ONVIF Simulator SHA-256 校验失败"
        }
        $extract = Join-Path $LabRoot "extract-$OnvifVersion"
        Expand-Archive -LiteralPath $OnvifZip -DestinationPath $extract -Force
        Copy-Item -LiteralPath (Join-Path $extract "onvif-simulator.exe") -Destination $OnvifExe
    }
    docker compose -f $ComposeFile pull
    if ($LASTEXITCODE -ne 0) { throw "Toxiproxy 镜像拉取失败" }
    docker pull $FfmpegImage | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "FFmpeg 媒体夹具镜像拉取失败" }
    Write-Output "BOOTSTRAP_OK"
}

function Initialize-MediaAndOnvifConfig {
    $mediaRoot = Split-Path -Parent $BlackVideoPath
    New-Item -ItemType Directory -Force -Path $mediaRoot | Out-Null
    if (-not (Test-Path $BlackVideoPath)) {
        docker run --rm -v "${LabRoot}:/work" $FfmpegImage `
            -f lavfi -i "color=c=black:s=640x360:r=15" -t 5 `
            -c:v libx264 -pix_fmt yuv420p -an -y /work/media/black.mp4 | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $BlackVideoPath)) {
            throw "黑色视频夹具生成失败"
        }
    }
    $onvifPassword = [guid]::NewGuid().ToString("N")
    Set-Content -LiteralPath $OnvifCredentialPath -Value $onvifPassword -NoNewline
    $config = Get-Content -Raw $OnvifConfig | ConvertFrom-Json
    $config.media.profiles = @([ordered]@{
        name = "main"
        token = "profile_main"
        kind = "file"
        media_file_path = $BlackVideoPath
        video_source_token = "VS_MAIN"
    })
    $config.auth.enabled = $true
    $config.auth.users = @([ordered]@{
        username = "device-lab"
        password = $onvifPassword
        role = "Administrator"
    })
    $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $RuntimeOnvifConfig
    & $OnvifExe config validate -config $RuntimeOnvifConfig
    if ($LASTEXITCODE -ne 0) { throw "ONVIF 运行时配置校验失败" }
}

function Test-TcpPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        return $task.Wait(1000) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Wait-Http([string]$Uri, [int]$Attempts = 30) {
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
            if ($response.status -eq "ok") { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "服务未就绪: $Uri"
}

function Invoke-Start {
    Invoke-Stop -Quiet
    Invoke-Bootstrap
    Initialize-MediaAndOnvifConfig
    $credential = [guid]::NewGuid().ToString("N")
    Set-Content -LiteralPath $CredentialPath -Value $credential -NoNewline
    $env:SECURITY_DIAGNOSIS_LAB_CREDENTIAL = $credential

    $contract = Start-Process -FilePath "uv" -ArgumentList @(
        "run", "python", "scripts/run_device_lab_contract.py"
    ) -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput (Join-Path $RuntimeRoot "contract.out.log") `
      -RedirectStandardError (Join-Path $RuntimeRoot "contract.err.log")
    $onvif = Start-Process -FilePath $OnvifExe -ArgumentList @(
        "serve", "-config", $RuntimeOnvifConfig, "-log-file", "-"
    ) -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput (Join-Path $RuntimeRoot "onvif.out.log") `
      -RedirectStandardError (Join-Path $RuntimeRoot "onvif.err.log")
    try {
        docker compose -f $ComposeFile up -d
        if ($LASTEXITCODE -ne 0) { throw "Toxiproxy 启动失败" }
        Wait-Http "http://127.0.0.1:28082/health"
        Wait-Http "http://127.0.0.1:28081/health"
        for ($i = 0; $i -lt 30 -and -not (Test-TcpPort 28083); $i++) {
            Start-Sleep -Milliseconds 500
        }
        if (-not (Test-TcpPort 28083)) { throw "ONVIF HTTP 端口未就绪" }
        if (-not (Test-TcpPort 28080)) { throw "ONVIF HTTP 代理端口未就绪" }
        @{ contract_pid = $contract.Id; onvif_pid = $onvif.Id } |
            ConvertTo-Json | Set-Content -LiteralPath $StatePath
        Write-Output "DEVICE_LAB_STARTED"
    } catch {
        Stop-Process -Id $contract.Id, $onvif.Id -Force -ErrorAction SilentlyContinue
        docker compose -f $ComposeFile down | Out-Null
        throw
    }
}

function Invoke-Verify {
    if (-not (Test-Path $CredentialPath)) { throw "Device Lab 尚未启动" }
    $env:SECURITY_DIAGNOSIS_LAB_CREDENTIAL = Get-Content -Raw $CredentialPath
    uv run python scripts/probe_device_lab.py
    if ($LASTEXITCODE -ne 0) { throw "Toxiproxy 故障注入探针失败" }
    uv run python scripts/eval_device_lab_scenarios.py
    if ($LASTEXITCODE -ne 0) { throw "Device Lab 八场景矩阵失败" }
}

function Invoke-Status {
    $contract = $false
    $onvif = $false
    if (Test-Path $StatePath) {
        $state = Get-Content -Raw $StatePath | ConvertFrom-Json
        $contract = [bool](Get-Process -Id $state.contract_pid -ErrorAction SilentlyContinue)
        $onvif = [bool](Get-Process -Id $state.onvif_pid -ErrorAction SilentlyContinue)
    }
    $toxiproxy = Test-TcpPort 28474
    [ordered]@{ contract = $contract; onvif = $onvif; toxiproxy = $toxiproxy } |
        ConvertTo-Json
}

function Invoke-Stop([switch]$Quiet) {
    if (Test-Path $StatePath) {
        $state = Get-Content -Raw $StatePath | ConvertFrom-Json
        Stop-Process -Id $state.contract_pid, $state.onvif_pid -Force -ErrorAction SilentlyContinue
    }
    docker compose -f $ComposeFile down --remove-orphans | Out-Null
    Remove-Item -LiteralPath $StatePath, $CredentialPath, $OnvifCredentialPath, `
        $RuntimeOnvifConfig `
        -Force -ErrorAction SilentlyContinue
    if (-not $Quiet) { Write-Output "DEVICE_LAB_STOPPED" }
}

switch ($Command) {
    "Bootstrap" { Invoke-Bootstrap }
    "Start" { Invoke-Start }
    "Verify" { Invoke-Verify }
    "Status" { Invoke-Status }
    "Stop" { Invoke-Stop }
}
