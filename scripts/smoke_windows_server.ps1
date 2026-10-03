param(
    [string]$ServerPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $ServerPath) {
    $ServerPath = Join-Path $Root "build\windows\pyinstaller-dist\heliostat-viewer-server\heliostat-viewer-server.exe"
}
if (-not (Test-Path $ServerPath)) {
    throw "Windows calculation service was not built: $ServerPath"
}

$statePath = Join-Path ([System.IO.Path]::GetTempPath()) "heliostat-server-smoke-$PID"
$portPath = Join-Path $statePath "server.port"
$stdoutPath = Join-Path $statePath "stdout.log"
$stderrPath = Join-Path $statePath "stderr.log"
New-Item $statePath -ItemType Directory -Force | Out-Null
$serverProcess = $null

function Get-ServiceLogs {
    $output = @()
    if (Test-Path $stdoutPath) { $output += "--- stdout ---"; $output += Get-Content $stdoutPath }
    if (Test-Path $stderrPath) { $output += "--- stderr ---"; $output += Get-Content $stderrPath }
    return ($output -join [Environment]::NewLine)
}

try {
    $serverProcess = Start-Process -FilePath $ServerPath `
        -ArgumentList @("--port", "0", "--port-file", $portPath) `
        -WorkingDirectory (Split-Path $ServerPath) -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        if ($serverProcess.HasExited) {
            throw "Windows calculation service exited during startup (exit code $($serverProcess.ExitCode)).`n$(Get-ServiceLogs)"
        }
        if (Test-Path $portPath) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path $portPath)) {
        throw "Windows calculation service did not publish a port within 20 seconds.`n$(Get-ServiceLogs)"
    }

    $port = (Get-Content $portPath -Raw).Trim()
    if ($port -notmatch '^\d+$') { throw "Service wrote an invalid port: $port" }
    $reply = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 15
    if ($reply.StatusCode -ne 200) { throw "Health endpoint returned HTTP $($reply.StatusCode)" }
    Write-Host "Windows calculation-service smoke test passed: http://127.0.0.1:$port/api/health"
}
finally {
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
        $serverProcess.WaitForExit()
    }
    if (Test-Path $statePath) { Remove-Item $statePath -Recurse -Force }
}
