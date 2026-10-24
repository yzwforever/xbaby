param([Parameter(Mandatory=$true)][string]$PythonPath, [string]$At='21:30', [switch]$Remove)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$digest = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot))
$suffix = ([BitConverter]::ToString($digest)).Replace('-','').Substring(0,8)
$taskName = 'NewsStudy-' + $suffix
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    exit 0
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Python executable not found' }
if ($At -notmatch '^([01][0-9]|2[0-3]):[0-5][0-9]$') { throw 'Invalid HH:mm' }
$pythonWindowless = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (Test-Path -LiteralPath $pythonWindowless) { $PythonPath = $pythonWindowless }
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument '-m newsstudy run' -WorkingDirectory $projectRoot
$userIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userIdentity
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At $At
$repeatTemplate = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 1)
$dailyTrigger.Repetition = $repeatTemplate.Repetition
$principal = New-ScheduledTaskPrincipal -UserId $userIdentity -LogonType Interactive -RunLevel Limited
$options = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$definition = New-ScheduledTask -Action $action -Trigger @($loginTrigger,$dailyTrigger) -Principal $principal -Settings $options -Description ('NewsStudy local daily study report: '+$projectRoot)
Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
Write-Output $taskName
