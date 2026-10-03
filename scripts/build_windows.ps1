param([string]$Version = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Version) { $Version = (Get-Content "$Root\VERSION" -Raw).Trim() }
$Build = "$Root\build\windows"
$Output = "$Root\dist\windows"
$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "$env:USERPROFILE\venv\Scripts\python.exe" }
if (-not (Test-Path $Python)) { throw "Python virtual environment not found: $Python" }
Remove-Item $Build,$Output -Recurse -Force -ErrorAction SilentlyContinue
New-Item $Build,$Output,"$Root\windows\assets" -ItemType Directory -Force | Out-Null

& $Python -m maturin build --release --manifest-path "$Root\rust\heliostat-core\Cargo.toml" `
  --features python-bindings --out "$Build\wheels"
& $Python -m pip install --force-reinstall (Get-ChildItem "$Build\wheels\*.whl" | Select-Object -First 1).FullName
& $Python -m PyInstaller --noconfirm --clean --onedir --name heliostat-viewer-server `
  --distpath "$Build\pyinstaller-dist" --workpath "$Build\pyinstaller-work" `
  --specpath "$Build\pyinstaller-spec" --paths $Root --collect-data pvlib `
  --hidden-import _heliostat_rust `
  --add-data "$Root\viewer;viewer" `
  --add-data "$Root\data\gemasolar_config.json;data" `
  --add-data "$Root\data\power_tower_catalog.json;data" `
  --add-data "$Root\data\processed\gemasolar_layout.csv;data\processed" `
  --add-data "$Root\data\processed\gemasolar_dni_2023.csv;data\processed" `
  --add-data "$Root\reports\three_layout_optimization;reports\three_layout_optimization" `
  "$Root\desktop\server_entry.py"

dotnet publish "$Root\windows\HeliostatViewer\HeliostatViewer.csproj" -c Release -r win-x64 `
  --self-contained true -p:PublishSingleFile=false -o "$Output\app"
Copy-Item "$Build\pyinstaller-dist\heliostat-viewer-server" "$Output\app\server" -Recurse
Compress-Archive -Path "$Output\app\*" -DestinationPath "$Output\Heliostat-Viewer-Windows-x64-v$Version-portable.zip"

$Bootstrapper = "$Root\windows\assets\MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $Bootstrapper)) {
  Invoke-WebRequest "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $Bootstrapper
}
$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) { throw "Inno Setup 6 is required to create the installer." }
& $Iscc "/DAppVersion=$Version" "/DSourceDir=$Output\app" "/DOutputDir=$Output" "$Root\windows\installer.iss"
Get-ChildItem "$Output\*.zip","$Output\*.exe" | ForEach-Object {
  $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
  "$hash  $($_.Name)" | Set-Content "$($_.FullName).sha256"
}
