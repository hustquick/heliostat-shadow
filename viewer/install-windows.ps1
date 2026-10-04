param([string]$Target, [string]$Staged, [int]$ParentId)
$ErrorActionPreference = 'Stop'
$parent = Get-Process -Id $ParentId -ErrorAction SilentlyContinue
if ($parent -and -not $parent.WaitForExit(120000)) { throw 'Application did not exit' }
# Keep the existing install directory (and uninstaller) for installed and portable builds.
$backup = Join-Path $env:LOCALAPPDATA ('Heliostat Viewer\Updates\backup-' + [DateTime]::UtcNow.Ticks)
New-Item $backup -ItemType Directory -Force | Out-Null
$copied = @()
function Get-StagedFiles([string]$Directory, [string]$Relative = '') {
    foreach ($item in Get-ChildItem -LiteralPath $Directory) {
        $name = if ($Relative) { Join-Path $Relative $item.Name } else { $item.Name }
        if ($item.PSIsContainer) { Get-StagedFiles $item.FullName $name }
        else { [PSCustomObject]@{ Source = $item.FullName; Relative = $name } }
    }
}
try {
    Get-StagedFiles $Staged | ForEach-Object {
        $relative = $_.Relative
        $dest = Join-Path $Target $relative
        $old = Join-Path $backup $relative
        if (Test-Path $dest) { New-Item (Split-Path $old) -ItemType Directory -Force | Out-Null; Copy-Item $dest $old }
        New-Item (Split-Path $dest) -ItemType Directory -Force | Out-Null
        $copied += $relative
        Copy-Item -LiteralPath $_.Source -Destination $dest -Force
    }
} catch {
    foreach ($relative in $copied) {
        $old = Join-Path $backup $relative; $dest = Join-Path $Target $relative
        if (Test-Path $old) { Copy-Item $old $dest -Force } else { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
    }
    throw
}
Start-Process (Join-Path $Target 'HeliostatViewer.exe')
