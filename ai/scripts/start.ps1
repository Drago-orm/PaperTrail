#Requires -Version 7.0
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$processes = @()
$exitCode = 1

function Assert-PortAvailable {
    param([int]$Port)

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
    }
    catch {
        throw "本地端口 $Port 已占用，未终止占用它的进程。"
    }
    finally {
        $listener.Stop()
    }
}

try {
    $nodePath = (Get-Command node -ErrorAction Stop).Source
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    $uvPath = if ($uvCommand) { $uvCommand.Source } else { Join-Path $projectRoot '.tools/bin/uv.exe' }
    if (-not (Test-Path -LiteralPath $uvPath -PathType Leaf)) {
        throw 'uv 缺失，请按 README 安装 uv 后重试。'
    }
    $frontendRoot = Join-Path $projectRoot 'frontend'
    $backendRoot = Join-Path $projectRoot 'backend'
    $vitePath = Join-Path $frontendRoot 'node_modules/vite/bin/vite.js'
    if (-not (Test-Path -LiteralPath $vitePath -PathType Leaf)) {
        throw '前端依赖缺失，请先执行 .\scripts\check.ps1 -Scope all -Install。'
    }

    Assert-PortAvailable 8000
    Assert-PortAvailable 5173
    Push-Location -LiteralPath $backendRoot
    try {
        & $uvPath sync --locked
        if ($LASTEXITCODE -ne 0) {
            $exitCode = $LASTEXITCODE
            throw "后端依赖同步失败，退出码 $exitCode。"
        }
    }
    finally {
        Pop-Location
    }
    $pythonPath = Join-Path $backendRoot '.venv/Scripts/python.exe'
    $logDirectory = Join-Path $projectRoot 'data/logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $runId = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID

    # Run Python directly so a failed uv wrapper cannot leave its server behind.
    $backend = Start-Process -FilePath $pythonPath -ArgumentList @(
        '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'
    ) -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDirectory "$runId-backend.out.log") `
        -RedirectStandardError (Join-Path $logDirectory "$runId-backend.err.log")
    $processes += $backend

    # Start-Process joins arguments into a command line; quote the single file path.
    $frontend = Start-Process -FilePath $nodePath -ArgumentList @(
        ('"{0}"' -f $vitePath), '--host', '127.0.0.1', '--port', '5173', '--strictPort'
    ) -WorkingDirectory $frontendRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDirectory "$runId-frontend.out.log") `
        -RedirectStandardError (Join-Path $logDirectory "$runId-frontend.err.log")
    $processes += $frontend
    Write-Output "启动日志：$logDirectory（$runId）"

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $ready = $false
    $lastStartupError = '服务尚未就绪'
    while ([DateTime]::UtcNow -lt $deadline) {
        foreach ($process in $processes) {
            if ($process.HasExited) {
                $exitCode = [Math]::Max(1, $process.ExitCode)
                throw "服务启动时退出，退出码 $($process.ExitCode)，请检查启动日志。"
            }
        }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1
            $page = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -TimeoutSec 1
            if ($health.status -ne 'ok' -or $page.StatusCode -ne 200) {
                throw '服务响应未满足健康检查。'
            }
            $ready = $true
            break
        }
        catch {
            $lastStartupError = $_.Exception.Message
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $ready) {
        throw "服务在 30 秒内未就绪：$lastStartupError。请检查启动日志。"
    }

    Write-Output 'PaperTrail 已启动：http://127.0.0.1:5173/（Ctrl+C 停止前后端）'
    while ($true) {
        foreach ($process in $processes) {
            if ($process.HasExited) {
                $exitCode = [Math]::Max(1, $process.ExitCode)
                throw "服务已退出，退出码 $($process.ExitCode)，正在清理其他服务。"
            }
        }
        Start-Sleep -Milliseconds 250
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
}
finally {
    foreach ($process in $processes) {
        try {
            if (-not $process.HasExited) {
                $process.Kill($true)
                $process.WaitForExit()
            }
        }
        catch {
            [Console]::Error.WriteLine("清理服务进程失败：$($_.Exception.Message)")
            $exitCode = 1
        }
        finally {
            $process.Dispose()
        }
    }
}

exit $exitCode
