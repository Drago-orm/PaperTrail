#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('frontend', 'backend', 'all')]
    [string]$Scope = 'all',
    [switch]$Install
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-CheckedCommand {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

try {
    if ($Scope -in @('frontend', 'all')) {
        Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
        try {
            if ($Install) {
                Invoke-CheckedCommand 'npm.cmd' @('ci')
            }
            Invoke-CheckedCommand 'npm.cmd' @('run', 'build')
        }
        finally {
            Pop-Location
        }
    }

    if ($Scope -in @('backend', 'all')) {
        $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
        $uvPath = if ($uvCommand) { $uvCommand.Source } else { Join-Path $projectRoot '.tools/bin/uv.exe' }
        Push-Location -LiteralPath (Join-Path $projectRoot 'backend')
        try {
            if ($Install) {
                Invoke-CheckedCommand $uvPath @('sync', '--locked')
            }
            Invoke-CheckedCommand $uvPath @('run', '--locked', 'python', '-m', 'compileall', '-q', 'app')
            Invoke-CheckedCommand $uvPath @('run', '--locked', 'python', '-m', 'pytest', '-q')
        }
        finally {
            Pop-Location
        }
    }

    Write-Output "检查通过：$Scope"
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
