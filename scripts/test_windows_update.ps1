param([switch]$SkipUnitTests)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $root
try {
    # Tests use isolated install/user directories and never the working app.
    if (-not $SkipUnitTests) {
        python -m pytest tests/test_windows_update.py tests/test_updates.py -q
        if ($LASTEXITCODE -ne 0) { throw 'Windows updater tests failed' }
    }
    python -m scripts.verify_desktop_update (Join-Path $root 'dist/windows/app')
    if ($LASTEXITCODE -ne 0) { throw 'Real Windows startup/update/cleanup verification failed; inspect build/update-verification/runs' }
} finally { Pop-Location }
