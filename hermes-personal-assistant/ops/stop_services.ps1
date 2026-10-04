# Stops the assistant app and ngrok, whether they were started by the scheduled
# tasks or by hand. Ending a task alone leaves its child processes running.

foreach ($name in "Assistant_App", "Assistant_Ngrok") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name
    }
}

$patterns = "*run_assistant.ps1*", "*run_ngrok.ps1*", "*-m assistant.run*"
Get-CimInstance Win32_Process | Where-Object {
    $commandLine = $_.CommandLine
    $_.Name -eq "ngrok.exe" -or ($commandLine -and ($patterns | Where-Object { $commandLine -like $_ }))
} | ForEach-Object {
    Write-Host "Stopping $($_.Name) ($($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
