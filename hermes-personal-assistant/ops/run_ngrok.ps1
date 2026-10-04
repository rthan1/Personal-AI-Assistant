# Keeps the ngrok tunnel to the sign-up site running, restarting it if it exits.
# Launched hidden by the Assistant_Ngrok scheduled task; see install_tasks.ps1.
# Only port 8787 (the sign-up site) may be tunneled, never the bridge on 8788.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$ngrok = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\ngrok.exe"
$port = 8787
$logDir = Join-Path $root "data\logs"
$log = Join-Path $logDir "ngrok.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log([string]$message) {
    Add-Content -Path $log -Value "$(Get-Date -Format s) [supervisor] $message"
}

# The ngrok static domain is the host of PUBLIC_BASE_URL in the project .env.
$line = Get-Content (Join-Path $root ".env") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*PUBLIC_BASE_URL\s*=' } | Select-Object -First 1
if (-not $line) {
    Write-Log "PUBLIC_BASE_URL is not set in .env; not starting ngrok"
    exit 1
}
$domain = ([Uri](($line -split '=', 2)[1].Trim().Trim('"', "'"))).Host

function Limit-LogSize {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 20MB) {
        Move-Item -Force $log "$log.1"
    }
}

while ($true) {
    Limit-LogSize
    Write-Log "starting ngrok for $domain -> $port"
    # This ngrok version takes --domain, not --url.
    & $ngrok http "--domain=$domain" $port --log=$log --log-format=logfmt
    Write-Log "ngrok exited with code $LASTEXITCODE; restarting in 10s"
    Start-Sleep -Seconds 10
}
