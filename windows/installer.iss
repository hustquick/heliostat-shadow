#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\windows\app"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\windows"
#endif

[Setup]
AppId={{C0DB0373-E83C-4CE8-B0A6-C58C66F4D7C7}
AppName=塔式镜场设计与优化
AppVersion={#AppVersion}
AppPublisher=hustquick
AppPublisherURL=https://github.com/hustquick/heliostat-shadow
DefaultDirName={localappdata}\Programs\Heliostat Viewer
DefaultGroupName=塔式镜场设计与优化
OutputDir={#OutputDir}
OutputBaseFilename=Heliostat-Viewer-Windows-x64-v{#AppVersion}-Setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=HeliostatViewer\app.ico
UninstallDisplayIcon={app}\HeliostatViewer.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\塔式镜场设计与优化"; Filename: "{app}\HeliostatViewer.exe"
Name: "{autodesktop}\塔式镜场设计与优化"; Filename: "{app}\HeliostatViewer.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "检查 WebView2 运行环境…"; Flags: waituntilterminated
Filename: "{app}\HeliostatViewer.exe"; Description: "启动塔式镜场设计与优化"; Flags: nowait postinstall skipifsilent
