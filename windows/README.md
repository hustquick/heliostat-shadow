# Windows 应用

Windows 版使用 WebView2 原生窗口，启动随安装包提供的 Python 服务和 Rust 几何内核。运行时不显示终端，也不会打开外部浏览器。用户导入数据保存在 `%LOCALAPPDATA%\Heliostat Viewer`。

构建机需要 Windows x64、Python `~/venv`、Rust、.NET 8 SDK 和 Inno Setup 6：

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt
.\scripts\build_windows.ps1
```

输出包括普通安装程序与便携 ZIP，位于 `dist/windows/`。安装程序会静默检查 Microsoft Edge WebView2 Runtime。
