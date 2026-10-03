# macOS 应用与 DMG

`塔式镜场设计与优化` 使用原生 `WKWebView` 在应用窗口内显示三维镜场与二维投影，不会打开外部浏览器。
应用内部包含 Python 运行时、计算依赖、前端资源、Gemasolar 重建布局和 2023 年历史 DNI，
最终用户不需要安装 Python 或配置 `~/venv`。

应用包含可切换的公开塔式电厂参数目录、CSV 镜位导入和三类镜场重排。用户电厂与重排结果
保存在 `~/Library/Application Support/Heliostat Viewer/`，升级应用不会覆盖这些数据。

在 Apple Silicon Mac 上构建：

```bash
source "$HOME/venv/bin/activate"
python -m pip install -r requirements-build.txt
./scripts/build_macos_app.sh 1.0.0
```

输出位于 `dist/macos/`：

- `塔式镜场设计与优化.app`
- `Heliostat-Viewer-macOS-arm64-v<版本号>.dmg`
- DMG 的 SHA-256 校验文件

DMG 中包含应用和 Applications 快捷方式，可按标准方式拖入 Applications 安装。
当前公开构建为 Apple Silicon、macOS 12 或更高版本。应用使用本地临时签名；未使用
Developer ID 和 Apple 公证，因此首次启动下载版本时可能需要右键应用并选择“打开”。
