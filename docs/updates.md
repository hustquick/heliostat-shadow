# 桌面更新与四端发布

macOS、Windows 保留“帮助 → 检查更新”入口。点击后检查固定 GitHub Releases 正式发布，兼容新版自动下载、校验、安装并交接重启；面板分别显示检查、下载进度、校验、安装、等待启动、成功、失败、取消。下载和校验可以取消，安装已交接后须使用恢复入口。关闭更新面板不停止后台状态轮询或重启交接。

Android 按本次调整保留 APK 手动覆盖安装，与 iOS 一样没有应用内更新入口；两端的安装和计算流程没有改动。

客户端先用内置公钥验证 RSA 签名清单，再校验固定仓库下载路径、平台、渠道、架构、最低系统版本、ZIP 大小、SHA-256 和包内版本。旧清单没有新增渠道字段时，沿用已有 macos-arm64/windows-x64 平台键。SHA-256 验证完整性，RSA 清单提供来源认证。安装前，以及旧进程退出后，重新验证授权和 ZIP 并重新解压，避免信任已经修改的解压目录。macOS 另执行 codesign 深度严格校验。

下载目录在用户应用支持目录的 `Heliostat Viewer/Updates/<应用路径散列>/heliostat-update-*` 下。`owner.json` 标记、持久化会话和安装事务共同确认归属。设置和镜场数据不参与安装替换或清理；桌面启停参数在退出前存入用户数据目录的 `desktop-preferences.json`，新版在首帧初始化前恢复，解决随机 localhost 端口及 macOS 临时 WebView 存储导致的设置丢失。这个设置文件位于 Updates 之外。`HELIOSTAT_VIEWER_DATA` 同时隔离计算数据、更新、WebView2 和测试日志。

macOS 使用从旧应用复制到私有下载目录的独立服务运行时执行事务，原应用保持原名称和原路径。旧应用临时移到准确的 `<应用路径>.previous`，替换失败恢复旧目录。只有包内版本及 Bundle 构建号匹配，关键服务健康，元数据、首帧、目标投影和主界面初始化完成，WebGL 上下文可用后，原生窗口才确认启动成功。确认携带该原生进程启动时缓存的版本，旧窗口不能用磁盘上已经替换的新版本冒充启动成功。`open` 成功本身不会触发清理。确认后删除本次备份和暂存文件。

新版启动失败时，先退出应用；助手等待启动的超时结束后，双击 `<应用路径>.update-state/recover.command` 恢复并启动旧版。该命令使用独立运行时，不依赖原应用路径仍存在；恢复与安装共用锁。状态及错误在 `.update-state/state.json`、`errors.log`，安装日志在私有 Updates 目录。恢复旧版正常启动后，清理本次失败新版副本和暂存目录。

macOS 历史 `.previous` 只检查当前应用的准确路径，须 Bundle 身份、较旧构建号、包内版本和 codesign 都通过，且当前版本实际初始化成功。清理前建立迁移事务并记录备份目录的设备号和 inode。部分删除后即使 Info.plist 已被删除，仍可安全重试；目录被用户换成其他备份则拒绝删除。不能确认归属的历史文件保留，错误记录到 `~/Library/Logs/heliostat-update.log`。不会扫描 Applications 的其他目录。此前修复的旧格式事务可在新版健康确认后迁移；缺失归属证据的残缺旧备份仍保留。

Windows 保留现有每用户安装/便携 ZIP 的逐文件更新方式，保留安装目录和卸载器。安装助手先等待旧原生进程退出，对全部将覆盖的应用文件创建备份，再在每次修改前持久化恢复日志。对占用文件进行有限重试；失败时按日志还原，恢复失败则保留备份和诊断信息。不申请额外管理员权限，不在原地强行覆盖仍运行的应用，不安排未经用户同意的系统重启。目录不可写时明确失败，用户可把应用安装到可写目录后重试。

Windows 事务在 `%LOCALAPPDATA%/Heliostat Viewer/Updates/<应用路径散列>/transaction`，恢复入口是 `recover.cmd`，运行前退出应用和它的计算服务。WebView2 主界面和服务完成与 macOS 相同的初始化检查后，确认当前包版本并清理备份。助手无法删除自身被 Windows 占用的运行时，由已安装的新版启动独立确认进程，等待安装助手释放锁后接续清理；失败在后续正常启动重试。旧实现遗留的无事务 `backup-*` 不扫描、不删除，因为无法确认其归属和健康状态。

中断检查/下载/校验会显示可重试错误，已就绪的 ZIP 在下次启动重新认证。跨窗口的任务文件锁防止并发检查、下载和安装；替换中的事务保留恢复入口，不能开始第二个安装任务。设备重启不会依赖系统临时目录保存恢复程序。成功后的清理失败保留事务和错误，后续初始化成功后重试；不会因清理失败回滚已正常启动的新版。

首次兼容性：已经发布的旧版本仍运行它们自带的助手。第一次升级到本次修改不能获得新代码在替换前提供的持久化、启动失败恢复或设置快照能力；新版健康后可以处理能确认归属的 macOS `.previous`，不能找回旧版已丢失的内存设置或定位未记账的临时目录。Windows 的旧助手备份没有新事务，不自动清理。重要首次迁移可用现有安装包手动覆盖，并保留用户数据。后续两个包含本修改的版本之间才完整具备新事务机制。

验证命令：

```sh
python -m pytest tests/test_updates.py tests/test_macos_update.py tests/test_windows_update.py -q
node tests/update_ui.mjs
node tests/update_health.mjs
xcrun swiftc -typecheck -framework AppKit -framework WebKit macos/HeliostatViewerApp.swift
python -m scripts.verify_desktop_update '/路径/测试应用.app'
python -m scripts.verify_macos_update_failures '/路径/测试应用.app'
```

后两个命令只复制应用到 `build/update-verification` 的隔离目录，以测试密钥和本地传输适配验证实际原生启动、打包助手、签名、替换、恢复和清理。生产客户端不接受任意本地更新来源。故障脚本使用真实 macOS codesign、权限/不可变标志、退出的原生程序及失效计算数据；中断通过事务检查点注入，并没有真的重启设备。测试过程仅终止隔离目录中的进程。

当前验证记录：41 项更新相关 Python 自动化通过，1 项真实 Windows 文件共享测试因当前不是 Windows 跳过；两个 JS 测试及 Swift 类型检查通过。较早迭代的 macOS 隔离原生更新曾通过。最终加强版本缓存和主界面就绪确认后，在 Mac 锁屏环境中回归超时，没有健康确认；备份和暂存目录按预期保留，不能把最终完整 GUI 更新和清理算作实机通过。最终打包服务的 health、metadata、frame、target、启停参数恢复接口已通过实际进程和 HTTP 检查。最终故障脚本已实测网络失败、损坏包、RSA 不匹配、无新版本、取消、实际文件系统拒绝替换、真实 codesign 拒绝篡改资源；退出的原生程序保留备份，生成的恢复命令实际还原旧包及用户数据，但恢复后初始化/清理因 GUI 未确认而超时，后续中断和清理故障步骤未完成最终回归。较早故障迭代曾完成这些步骤，不能替代最终代码验收。原始记录在 `build/update-verification`。隔离包使用当前 Python 计算回退路径，没有重新构建 Rust 生产内核；不能代表四端生产安装包全量验收。真实 GitHub v1.0.6 的 macOS 和 Windows ZIP 已下载并通过 RSA 清单、完整性、包内版本检查，macOS 同时通过真实 codesign；没有安装这些 GitHub 包。

当前主机没有 Windows/WebView2 运行环境。C# 已使用隔离的 .NET SDK 在 macOS 上交叉编译通过（0 错误，1 项现有 WindowsBase/WebView2.Wpf 依赖警告），这不代表 Windows 运行验证。真实 Windows 替换、进程占用、权限、恢复和自动清理尚未实机验证。Windows 构建机运行 `scripts/test_windows_update.ps1`，它运行相关测试及使用真实已构建应用的隔离更新验证；存在真实 Windows 文件共享测试。真实设备重启没有验证；当前测试仅注入持久化中断状态。用户设置和镜场数据的检查覆盖测试设置与镜场文件哈希，以及桌面启停参数持久化，不代表所有用户自定义数据组合已经验收。

构建和发布继续使用 `scripts/build_macos_app.sh`、`scripts/build_windows.ps1` 和现有四端发布框架。版本来源仍为 VERSION/BUILD_NUMBER；正式发布前递增两者、更新 RELEASE_NOTES.md，完成生产 Rust 内核构建和两端隔离验证，再收齐既有四端产物，由现有发布脚本生成签名清单。新清单为桌面加入渠道、系统、架构和最低系统版本字段。公开版本不能覆盖。当前工作没有改版本、打标签、提交推送或发布；现有工作流推送 main 会触发发布，因此验收前不要推送 main。
