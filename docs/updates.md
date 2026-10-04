# 桌面更新与四端发布

macOS、Windows 在“帮助 → 检查更新”中查看版本和更新说明，下载完成后校验 RSA 签名清单、包大小、SHA-256 和包内版本，再安装并重启。移动端没有更新入口，保留电脑安装 iPhone、手动安装 Android 的方式。

首次安装 1.0.3 后才能使用桌面更新；1.0.2 没有更新程序。网络连接失败显示错误并允许重试，不会替换正在使用的应用。macOS 应用须安装在可写目录；不要直接从 DMG 运行后更新。

版本唯一来源为 `VERSION`、`BUILD_NUMBER`，每次发布都必须递增这两个文件，并更新 `RELEASE_NOTES.md`。构建脚本生成共享 `viewer/version.json`，iOS 构建命令与 Android 配置均读取同一版本。已公开版本禁止覆盖，发布脚本会拒绝重复发布和版本降级。

CI 等核心检查和四端构建成功后创建草稿，上传全部安装包和 `updates.json`，最后一次性公开发布。客户端从固定 GitHub 仓库的 latest release 获取清单，使用内置公钥校验。签名私钥由仓库 Actions secret `UPDATE_SIGNING_KEY` 保存，本机备份位于用户应用支持目录的 Signing 文件夹；不得提交私钥。Android CI 通过 `ANDROID_SIGNING_KEYSTORE` 使用固定签名身份。

之前不同机器生成的 Android 调试包可能签名不同，无法直接覆盖，需一次迁移到固定签名版本；不要先卸载而丢失数据，应先导出布局并确认备份。

桌面更新助手在应用退出后运行。macOS 旧应用保留为同目录的 `.previous`；替换失败恢复旧目录。Windows 备份位于 `%LOCALAPPDATA%/Heliostat Viewer/Updates/backup-*`，复制失败恢复已修改文件并保留原卸载器。用户数据目录不参与应用替换。备份保障安装失败回退，不等同于对所有新版运行错误自动回滚。

验算：`python -m pytest -q tests/test_updates.py`、`node tests/update_ui.mjs`。Windows 构建机另运行 `scripts/test_windows_update.ps1` 检查真实文件替换与锁定文件失败回退。
