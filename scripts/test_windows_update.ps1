$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$temp = Join-Path $env:TEMP ('heliostat-updater-test-' + [Guid]::NewGuid().ToString('N'))
$target = Join-Path $temp 'installed'; $stage = Join-Path $temp 'staged'
New-Item $target,$stage -ItemType Directory -Force | Out-Null
try {
    Set-Content (Join-Path $target 'data.txt') 'old'
    Set-Content (Join-Path $stage 'data.txt') 'new'
    Set-Content (Join-Path $target 'unins000.dat') 'preserve'
    Copy-Item "$env:WINDIR\System32\where.exe" (Join-Path $stage 'HeliostatViewer.exe')
    & "$root\viewer\install-windows.ps1" $target $stage 2147483647
    if ((Get-Content (Join-Path $target 'data.txt')) -ne 'new') { throw 'Replacement failed' }
    if ((Get-Content (Join-Path $target 'unins000.dat')) -ne 'preserve') { throw 'Uninstaller was removed' }
    Set-Content (Join-Path $target 'a.txt') 'original'
    Set-Content (Join-Path $stage 'a.txt') 'changed'
    Set-Content (Join-Path $target 'z.txt') 'locked'
    Set-Content (Join-Path $stage 'z.txt') 'replacement'
    $lock = [IO.File]::Open((Join-Path $target 'z.txt'), 'Open', 'ReadWrite', 'None')
    $failed = $false
    try { & "$root\viewer\install-windows.ps1" $target $stage 2147483647 } catch { $failed = $true } finally { $lock.Dispose() }
    if (-not $failed) { throw 'Locked file did not cause failure' }
    if ((Get-Content (Join-Path $target 'a.txt')) -ne 'original') { throw 'Rollback failed' }
    Write-Output 'PASS Windows replacement, uninstaller preservation and rollback'
} finally { Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue }
