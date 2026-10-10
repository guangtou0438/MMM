# Compatibility launcher for the calendar and personal-memory application.
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $PSScriptRoot
try {
    $mmmCalendarPython = Join-Path $PSScriptRoot '.venv-calendar/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $mmmCalendarPython)) {
        throw 'Create .venv-calendar and install requirements-calendar.lock.txt first.'
    }
    $mmmWebArgs = @($args)
    if ($args.Count -gt 0 -and $args[0] -eq 'web') {
        $mmmWebArgs = @($args | Select-Object -Skip 1)
    }
    & $mmmCalendarPython -m calendar_agent @mmmWebArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
