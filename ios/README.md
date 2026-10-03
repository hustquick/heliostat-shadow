# iOS / iPadOS 应用

iOS 版是完全离线的 SwiftUI/WKWebView 应用。App 内置交互界面、Rust 光学内核、18 个电厂/布局及逐时 DNI 数据，不连接桌面服务。Swift 通过 C ABI 调用 Rust，电厂切换、逐镜计算、CSV 导入、三类镜场重排均在设备本地完成；用户布局和当前选择使用 `UserDefaults` 持久化。

构建模拟器版本：

```bash
./scripts/build_ios.sh
```

真机安装需要 Apple 开发签名。在 Xcode 打开生成的 `ios/HeliostatViewerIOS.xcodeproj`，选择开发团队和设备后构建；也可使用仓库的 `scripts/install_ios_device.sh`。应用不声明本地网络权限，正常使用无需电脑或网络。
