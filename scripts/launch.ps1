param([switch]$Install)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython=Join-Path $projectRoot '.venv/Scripts/python.exe'
$bundledPython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (Test-Path -LiteralPath $venvPython) { $pythonPath=$venvPython }
elseif (Test-Path -LiteralPath $bundledPython) { $pythonPath=$bundledPython }
elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $pythonPath=(& py.exe -3 -c 'import sys; print(sys.executable)').Trim()
} else { throw 'Install Python 3.11+ with tkinter first, then run this file again.' }
if ($Install) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $pythonPath -m venv --system-site-packages (Join-Path $projectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
    }
    & $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
} else {
    & $pythonPath -m newsstudy gui
    if ($LASTEXITCODE -ne 0) { throw 'Application failed. See logs directory.' }
}
