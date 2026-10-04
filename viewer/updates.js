export function initUpdates(get, post) {
  const $ = id => document.getElementById(id);
  let state, timer;
  function render(data) {
    state = data;
    $("updateVersion").textContent = `当前版本 ${data.current} · 构建 ${data.build}` + (data.latest ? ` ｜ 最新 ${data.latest}` : "");
    const messages = {idle: "点击检查更新获取最新版本。", checking: "正在检查更新…", available: "发现新版本。", current: "当前已是最新版本。", downloading: `正在下载 ${data.progress || 0}%`, ready: "新版已下载并校验，可安装更新。", installing: "正在安装新版…", manual: "iPhone 请连接电脑，由电脑安装新版。", permission: "请允许本应用安装软件，返回后再次点击安装。", error: `更新失败：${data.error || "请重试"}`};
    $("updateMessage").textContent = messages[data.state] || "";
    if (!data.supported && data.state !== "manual") $("updateMessage").textContent += " 此环境不能直接替换应用，请安装完整桌面版。";
    $("updateNotes").textContent = data.notes || "";
    $("checkUpdates").hidden = data.state === "manual";
    $("checkUpdates").disabled = ["checking", "downloading", "installing"].includes(data.state);
    $("downloadUpdate").hidden = !data.supported || !["available", "error"].includes(data.state) || !data.latest;
    $("installUpdate").hidden = !["ready", "permission"].includes(data.state);
    $("installUpdate").textContent = data.platform === "android" ? "安装更新" : "安装并重启";
    $("updateProgress").hidden = data.state !== "downloading";
    $("updateProgress").value = data.progress || 0;
    if (["checking", "downloading"].includes(data.state)) timer = setTimeout(refresh, 700);
  }
  async function refresh() {
    clearTimeout(timer);
    try { render(await get("update/status")); }
    catch (error) { $("updateMessage").textContent = `更新接口连接失败：${error.message}`; }
  }
  async function action(name) {
    clearTimeout(timer);
    try {
      render(await post(`update/${name}`, {}));
      if (name === "install" && state.state === "installing" && state.platform !== "android") {
        window.webkit?.messageHandlers?.heliostatUpdateQuit?.postMessage("quit");
        window.chrome?.webview?.postMessage("update-quit");
      }
    } catch (error) { $("updateMessage").textContent = error.message; }
  }
  async function open() { $("updatesDialog").showModal(); await refresh(); if (state?.supported && ["idle", "current", "error"].includes(state.state)) await action("check"); }
  $("checkUpdates").onclick = () => action("check");
  $("downloadUpdate").onclick = () => action("download");
  $("installUpdate").onclick = () => action("install");
  $("closeUpdates").onclick = () => { clearTimeout(timer); $("updatesDialog").close(); };
  return open;
}
