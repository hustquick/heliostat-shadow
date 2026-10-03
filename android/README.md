# Android 应用

Android 版是完全离线的原生 WebView 应用。APK 内置交互界面、三架构 Rust 光学库、18 个电厂/布局及逐时 DNI 数据，不连接桌面服务。电厂切换、逐镜计算、CSV 导入和三类镜场重排均在设备本地完成；用户布局和当前选择使用 `SharedPreferences` 持久化。

本地构建：

```bash
./scripts/build_android.sh
```

生成的调试签名 APK 可直接侧载测试。应用清单不申请 `INTERNET` 权限，运行不依赖电脑、局域网、Python、SciPy 或 Shapely。发布到 Google Play 前仍需使用正式发布密钥签名。
