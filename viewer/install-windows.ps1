param([string]$Target, [string]$Staged, [int]$ParentId)
$ErrorActionPreference = 'Stop'
# Legacy wrapper; the normal update entry now launches the copied bundled runtime
# directly. An existing signed transaction is required, never reconstruct one here.
$identity = [IO.Path]::GetFullPath($Target).TrimEnd([IO.Path]::DirectorySeparatorChar).ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
$key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($identity)))).Replace('-', '').ToLower().Substring(0,24)
$base = if ($env:HELIOSTAT_VIEWER_DATA) { $env:HELIOSTAT_VIEWER_DATA } else { Join-Path $env:LOCALAPPDATA 'Heliostat Viewer' }
$state = Join-Path $base "Updates\$key\transaction\state.json"
$runtime = Join-Path (Split-Path $Staged) 'helper-runtime\heliostat-viewer-server.exe'
if (-not (Test-Path -LiteralPath $state)) { throw 'No authorized update transaction exists' }
& $runtime --windows-update $state
if ($LASTEXITCODE -ne 0) { throw 'Update failed; consult the persistent transaction and recover.cmd' }
