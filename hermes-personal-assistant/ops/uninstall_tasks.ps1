# Stops and removes the Assistant_App and Assistant_Ngrok scheduled tasks.

& (Join-Path $PSScriptRoot "stop_services.ps1")
foreach ($name in "Assistant_App", "Assistant_Ngrok") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed $name"
    }
}
