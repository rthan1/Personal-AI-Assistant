# Registers the Assistant_App and Assistant_Ngrok scheduled tasks (start at logon,
# restart on failure), mirroring Hermes's Hermes_Gateway task. Safe to re-run.

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "launch_hidden.vbs"
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$userName = "$env:USERDOMAIN\$env:USERNAME"

function Get-TaskXml([string]$description, [string]$script) {
    $escapedArgs = [System.Security.SecurityElement]::Escape("//B //Nologo `"$launcher`" `"$script`"")
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$description</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$userSid</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Count>999</Count>
      <Interval>PT1M</Interval>
    </RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
  </Settings>
  <Triggers>
    <LogonTrigger>
      <UserId>$userName</UserId>
      <Delay>PT30S</Delay>
    </LogonTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>$escapedArgs</Arguments>
    </Exec>
  </Actions>
</Task>
"@
}

$tasks = @{
    "Assistant_App"   = @("Personal AI Assistant - bridge API and sign-up site", (Join-Path $PSScriptRoot "run_assistant.ps1"))
    "Assistant_Ngrok" = @("Personal AI Assistant - ngrok tunnel to the sign-up site", (Join-Path $PSScriptRoot "run_ngrok.ps1"))
}

foreach ($name in $tasks.Keys) {
    $description, $script = $tasks[$name]
    Register-ScheduledTask -TaskName $name -Xml (Get-TaskXml $description $script) -Force | Out-Null
    Write-Host "Registered $name"
}
Write-Host "Done. Start everything now with: .\demo_up.ps1"
