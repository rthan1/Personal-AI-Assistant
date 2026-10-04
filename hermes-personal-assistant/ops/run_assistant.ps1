# Keeps the assistant app (bridge API + sign-up site) running, restarting it if it exits.
# Launched hidden by the Assistant_App scheduled task; see install_tasks.ps1.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "data\logs"
$log = Join-Path $logDir "assistant.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

function Write-Log([string]$message) {
    Add-Content -Path $log -Value "$(Get-Date -Format s) [supervisor] $message"
}

function Limit-LogSize {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 20MB) {
        Move-Item -Force $log "$log.1"
    }
}

while ($true) {
    Limit-LogSize
    Write-Log "starting assistant.run"
    Push-Location (Join-Path $root "src")
    cmd /c "`"$python`" -m assistant.run >> `"$log`" 2>&1"
    $code = $LASTEXITCODE
    Pop-Location
    Write-Log "assistant.run exited with code $code; restarting in 10s"
    Start-Sleep -Seconds 10
}
