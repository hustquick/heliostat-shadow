using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace HustQuick.HeliostatViewer;

public sealed class ViewerForm : Form
{
    private readonly WebView2 webView = new() { Dock = DockStyle.Fill };
    private Process? server;
    private string? portFile;
    private StreamWriter? log;
    private bool closing;
    private string? launchedIdentity;

    public ViewerForm()
    {
        Text = "塔式镜场设计与优化";
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Width = 1360;
        Height = 860;
        MinimumSize = new Size(980, 650);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(8, 19, 26);
        Controls.Add(webView);
        CreateMenus();
        Shown += async (_, _) => await StartAsync();
        FormClosing += (_, _) => StopServer();
    }

    private void CreateMenus()
    {
        var menu = new MenuStrip();
        void Add(string title, params (string Label, string Command, Keys Shortcut)[] entries)
        {
            var group = new ToolStripMenuItem(title);
            foreach (var (label, command, shortcut) in entries)
            {
                var item = new ToolStripMenuItem(label) { ShortcutKeys = shortcut };
                item.Click += async (_, _) =>
                {
                    if (webView.CoreWebView2 is null) return;
                    await webView.ExecuteScriptAsync($"window.heliostatDesktopCommand?.('{command}')");
                };
                group.DropDownItems.Add(item);
            }
            menu.Items.Add(group);
        }
        Add("文件", ("导入镜面坐标…", "import", Keys.Control | Keys.O), ("导出全场坐标…", "export", Keys.Control | Keys.S));
        var exit = new ToolStripMenuItem("退出");
        exit.Click += (_, _) => Close();
        ((ToolStripMenuItem)menu.Items[0]).DropDownItems.Add(exit);
        Add("镜场", ("聚光启停参数…", "operation", Keys.None), ("镜场重排…", "rearrange", Keys.None), ("调整镜面朝向…", "orientation", Keys.None), ("恢复太阳跟踪", "reset", Keys.None));
        Add("视图", ("全场视角", "overview", Keys.Control | Keys.D1), ("聚焦目标", "focus", Keys.Control | Keys.D2), ("等轴测视图", "iso", Keys.Control | Keys.D3), ("俯视 E–N", "top", Keys.Control | Keys.D4), ("切换效率着色", "efficiency", Keys.None), ("切换目标塔着色", "tower", Keys.None), ("切换仅目标与候选镜", "neighbors", Keys.None));
        Add("图案", ("添加文字…", "text", Keys.None), ("生成当前文字图案", "textApply", Keys.None), ("添加图案图片…", "image", Keys.None), ("清除图案并恢复跟踪", "reset", Keys.None));
        Add("时间", ("播放 / 暂停", "play", Keys.None), ("上一小时", "prev", Keys.None), ("下一小时", "next", Keys.None));
        Add("帮助", ("检查更新…", "updates", Keys.None), ("操作说明", "help", Keys.None));
        Controls.Add(menu);
        MainMenuStrip = menu;
    }

    private async Task StartAsync()
    {
        try
        {
            launchedIdentity = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "server", "_internal", "viewer", "version.json"));
            var appData = Environment.GetEnvironmentVariable("HELIOSTAT_VIEWER_DATA") ?? Path.Combine(Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData), "Heliostat Viewer");
            Directory.CreateDirectory(appData);
            var webData = Path.Combine(appData, "WebView2");
            var environment = await CoreWebView2Environment.CreateAsync(null, webData);
            await webView.EnsureCoreWebView2Async(environment);
            webView.CoreWebView2.WebMessageReceived += (_, e) => {
                if (webView.Source?.ToString() != e.Source || !e.Source.StartsWith("http://127.0.0.1:")) return;
                var message = e.TryGetWebMessageAsString();
                if (message == "update-quit") Close();
                if (message == "update-healthy" && server is { HasExited: false })
                {
                    var helper = new ProcessStartInfo(Path.Combine(AppContext.BaseDirectory, "server", "heliostat-viewer-server.exe"))
                    { UseShellExecute = false, CreateNoWindow = true };
                    helper.Environment["HELIOSTAT_CONFIRM_IDENTITY"] = launchedIdentity ?? "";
                    helper.ArgumentList.Add("--windows-healthy");
                    helper.ArgumentList.Add(AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar));
                    try { Process.Start(helper)?.Dispose(); }
                    catch (Exception error) { WriteLog($"Update initialization confirmation failed: {error.Message}"); }
                }
            };
            webView.NavigateToString("""
                <!doctype html><meta charset="utf-8"><style>
                body{margin:0;height:100vh;display:grid;place-items:center;background:#08131a;color:#d9edf4;font:16px 'Segoe UI'}
                .spinner{width:28px;height:28px;margin:0 auto 18px;border:3px solid #264957;border-top-color:#4fc3dc;border-radius:50%;animation:s .8s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
                </style><div><div class="spinner"></div>正在启动塔式镜场设计与优化…</div>
                """);

            var executable = Path.Combine(AppContext.BaseDirectory, "server", "heliostat-viewer-server.exe");
            if (!File.Exists(executable)) throw new FileNotFoundException("内置计算服务不存在。", executable);
            portFile = Path.Combine(Path.GetTempPath(), $"heliostat-viewer-{Guid.NewGuid():N}.port");
            var logFolder = Path.Combine(appData, "Logs");
            Directory.CreateDirectory(logFolder);
            log = new StreamWriter(Path.Combine(logFolder, "heliostat-shadow-viewer.log"), false)
                { AutoFlush = true };
            var start = new ProcessStartInfo(executable, $"--port 0 --port-file \"{portFile}\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = Path.GetDirectoryName(executable)!,
            };
            start.Environment["HELIOSTAT_VIEWER_DATA"] = appData;
            start.Environment["HELIOSTAT_APP_PATH"] = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            start.Environment["HELIOSTAT_APP_PID"] = Environment.ProcessId.ToString();
            server = new Process { StartInfo = start, EnableRaisingEvents = true };
            server.OutputDataReceived += (_, e) => WriteLog(e.Data);
            server.ErrorDataReceived += (_, e) => WriteLog(e.Data);
            server.Exited += (_, _) => BeginInvoke(new Action(() =>
            {
                if (!closing) Fail("计算服务意外停止。日志位于本地应用数据的 Heliostat Viewer\\Logs。");
            }));
            if (!server.Start()) throw new InvalidOperationException("计算服务无法启动。");
            server.BeginOutputReadLine();
            server.BeginErrorReadLine();

            for (var attempt = 0; attempt < 300; attempt++)
            {
                if (server.HasExited) throw new InvalidOperationException("计算服务启动失败。");
                if (File.Exists(portFile) && int.TryParse((await File.ReadAllTextAsync(portFile)).Trim(), out var port))
                {
                    webView.CoreWebView2.Navigate($"http://127.0.0.1:{port}/");
                    return;
                }
                await Task.Delay(100);
            }
            throw new TimeoutException("计算服务启动超时。");
        }
        catch (Exception error)
        {
            Fail(error is WebView2RuntimeNotFoundException
                ? "缺少 Microsoft Edge WebView2 Runtime，请重新运行安装程序。"
                : $"应用无法启动：{error.Message}");
        }
    }

    private void WriteLog(string? line)
    {
        if (line is null || log is null) return;
        lock (log) log.WriteLine(line);
    }

    private void Fail(string message)
    {
        var appData = Environment.GetEnvironmentVariable("HELIOSTAT_VIEWER_DATA") ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Heliostat Viewer");
        var target = Path.GetFullPath(AppContext.BaseDirectory).TrimEnd(Path.DirectorySeparatorChar).ToLowerInvariant();
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(target))).ToLowerInvariant()[..24];
        var recovery = Path.Combine(appData, "Updates", key, "transaction", "recover.cmd");
        if (File.Exists(recovery)) message += $"\n更新恢复入口：{recovery}\n退出应用及计算服务，等待更新助手启动超时结束后运行此文件。";
        MessageBox.Show(this, message, "塔式镜场设计与优化", MessageBoxButtons.OK, MessageBoxIcon.Error);
        Close();
    }

    private void StopServer()
    {
        closing = true;
        if (server is { HasExited: false })
        {
            try { server.Kill(); server.WaitForExit(3000); } catch { }
        }
        server?.Dispose();
        log?.Dispose();
        if (portFile is not null) try { File.Delete(portFile); } catch { }
    }
}
