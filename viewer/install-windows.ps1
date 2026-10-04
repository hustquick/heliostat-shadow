param([string]$Target, [string]$Staged, [int]$ParentId)
$ErrorActionPreference = 'Stop'
$parent = Get-Process -Id $ParentId -ErrorAction SilentlyContinue
if ($parent -and -not $parent.WaitForExit(120000)) { throw 'Application did not exit' }
# Keep the existing install directory (and uninstaller) for installed and portable builds.
$backup = Join-Path $env:LOCALAPPDATA ('Heliostat Viewer\Updates\backup-' + [DateTime]::UtcNow.Ticks)
New-Item $backup -ItemType Directory -Force | Out-Null
$copied = @()
try {
    Get-ChildItem $Staged -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($Staged.Length).TrimStart('\')
        $dest = Join-Path $Target $relative
        $old = Join-Path $backup $relative
        if (Test-Path $dest) { New-Item (Split-Path $old) -ItemType Directory -Force | Out-Null; Copy-Item $dest $old }
        New-Item (Split-Path $dest) -ItemType Directory -Force | Out-Null
        $copied += $relative
        Copy-Item $_.FullName $dest -Force
    }
} catch {
    foreach ($relative in $copied) {
        $old = Join-Path $backup $relative; $dest = Join-Path $Target $relative
        if (Test-Path $old) { Copy-Item $old $dest -Force } else { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
    }
    throw
}
Start-Process (Join-Path $Target 'HeliostatViewer.exe')
