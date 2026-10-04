# Starts Hermes, the assistant app and ngrok (if not already running), then checks
# that each one responds. Run this before the demo.

$envLine = Get-Content (Join-Path (Split-Path -Parent $PSScriptRoot) ".env") -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^\s*PUBLIC_BASE_URL\s*=' } | Select-Object -First 1
if (-not $envLine) {
    Write-Host "PUBLIC_BASE_URL is not set in .env" -ForegroundColor Red
    exit 1
}
$publicUrl = ($envLine -split '=', 2)[1].Trim().Trim('"', "'").TrimEnd("/") + "/"

function Test-Hermes {
    [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*hermes_cli.main gateway run*" })
}

foreach ($name in "Hermes_Gateway", "Assistant_App", "Assistant_Ngrok") {
    if (-not (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
        Write-Host "MISSING  scheduled task $name (run install_tasks.ps1)" -ForegroundColor Red
        continue
    }
    # The Hermes task exits after launching a detached gateway, so IgnoreNew doesn't stop a second one.
    if ($name -eq "Hermes_Gateway" -and (Test-Hermes)) { continue }
    # Tasks use IgnoreNew, so this is a no-op for ones already running.
    Start-ScheduledTask -TaskName $name
}

function Test-Url([string]$url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10 `
            -Headers @{ "ngrok-skip-browser-warning" = "1" }
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

$checks = [ordered]@{
    "Hermes gateway process"            = { Test-Hermes }
    "Bridge API (127.0.0.1:8788)"       = { Test-Url "http://127.0.0.1:8788/health" }
    "Sign-up site (127.0.0.1:8787)"     = { Test-Url "http://127.0.0.1:8787/" }
    "Public sign-up page (ngrok)"       = { Test-Url $publicUrl }
}

$deadline = (Get-Date).AddSeconds(90)
do {
    $results = [ordered]@{}
    foreach ($check in $checks.Keys) { $results[$check] = & $checks[$check] }
    $allOk = -not ($results.Values -contains $false)
    if (-not $allOk) { Start-Sleep -Seconds 5 }
} while (-not $allOk -and (Get-Date) -lt $deadline)

foreach ($check in $results.Keys) {
    if ($results[$check]) {
        Write-Host "OK       $check" -ForegroundColor Green
    } else {
        Write-Host "FAIL     $check" -ForegroundColor Red
    }
}
if ($allOk) {
    Write-Host "`nAll good. Sign-up page: $publicUrl" -ForegroundColor Green
} else {
    Write-Host "`nSomething is down. Logs: data\logs\assistant.log, data\logs\ngrok.log, %LOCALAPPDATA%\hermes\logs\" -ForegroundColor Yellow
    exit 1
}
