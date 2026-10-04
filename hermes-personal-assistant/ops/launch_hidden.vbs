' Runs a PowerShell script with no console window and waits for it, so the
' scheduled task stays "Running" for as long as the script does.
' Usage: wscript.exe //B //Nologo launch_hidden.vbs <script.ps1>
Option Explicit
Dim sh, script
Set sh = CreateObject("WScript.Shell")
script = WScript.Arguments(0)
WScript.Quit sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """", 0, True)
