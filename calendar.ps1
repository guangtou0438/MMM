$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$calendarPython = Join-Path $PSScriptRoot '.venv-calendar/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $calendarPython)) { throw 'Create a Python 3.12 .venv-calendar and install requirements-calendar.txt first.' }
Push-Location $PSScriptRoot
try { & $calendarPython -m calendar_agent @args; exit $LASTEXITCODE } finally { Pop-Location }
