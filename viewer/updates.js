export function initUpdates(get, post) {
  const $ = id => document.getElementById(id);
  let state, timer, automatic = false, handoff = false, acting = false;
  function render(data) {
    state = data;
    $("updatesDialog").dataset.state = data.state;
    const hasNewVersion = data.latest && data.latest !== data.current && ["available", "downloading", "verifying", "ready", "installing", "waiting-start", "error"].includes(data.state);
    $("updateVersion").textContent = hasNewVersion ? `${data.current} → ${data.latest}` : `版本 ${data.current || "—"}`;
    const titles = {idle:"软件更新", checking:"正在检查更新", available:"发现新版本", current:"已是最新版本", downloading:"正在下载更新", ready:"更新已就绪", installing:"正在安装更新", verifying:"正在校验更新", "waiting-start":"等待新版启动", success:"更新成功", cancelled:"更新已取消", error:"暂时无法更新"};
    $("updatesTitle").textContent = titles[data.state] || "软件更新";
    $("updateStatusMark").textContent = {current:"✓", ready:"✓", available:"↓", downloading:"↓", error:"!"}[data.state] || "↻";
    const messages = {idle:"获取最新版本和改进说明。", checking:"", available:"安装后将重新启动应用。", current:"", downloading:`已下载 ${data.progress || 0}%`, ready:"新版已准备好，安装后将重新启动应用。", installing:"应用即将重新启动。", error:"请检查网络连接后重试。"};
    $("updateMessage").textContent = data.error || data.message || ({verifying:"正在验证签名、完整性和版本。", "waiting-start":"等待新版完成初始化；启动失败请退出应用后使用恢复入口。", success:data.cleanupPending ? "新版已正常启动，更新文件清理待重试。" : "新版已正常启动，更新完成。", cancelled:"当前应用仍可使用。"}[data.state]) || messages[data.state] || "";
    if (data.recovery && data.state === "error") $("updateMessage").textContent += ` 恢复入口：${data.recovery}`;
    $("updateMessage").hidden = !$("updateMessage").textContent;
    if (!data.supported) { $("updateMessage").hidden = false; $("updateMessage").textContent = "请使用完整桌面版进行更新。"; }
    const showRelease = ["available", "downloading", "ready", "installing"].includes(data.state) || (data.state === "current" && data.latest === data.current);
    $("updateNotesPanel").hidden = !showRelease || !data.notes?.trim();
    $("updateNotes").textContent = showRelease ? data.notes || "" : "";
    const date = /^\d{4}-\d{2}-\d{2}$/.test(data.date || "") ? data.date : "";
    $("updateDate").hidden = !showRelease || !date;
    $("updateDate").textContent = date ? `${date.replaceAll("-", ".")} 发布` : "";
    $("checkUpdates").hidden = !["idle", "current", "error", "cancelled", "success"].includes(data.state) || Boolean(hasNewVersion);
    $("checkUpdates").textContent = data.state === "error" ? "重试" : "检查更新";
    $("downloadUpdate").hidden = !data.supported || !["available", "error"].includes(data.state) || !hasNewVersion;
    $("downloadUpdate").textContent = data.state === "error" ? "重新下载" : "下载更新";
    $("installUpdate").hidden = data.state !== "ready";
    $("cancelUpdate").hidden = !["checking", "downloading", "verifying", "ready"].includes(data.state);
    $("dismissUpdates").textContent = ["available", "ready"].includes(data.state) ? "稍后" : "完成";
    $("updateProgress").hidden = data.state !== "downloading";
    $("updateProgress").value = data.progress || 0;
    if (["checking", "downloading", "verifying", "installing", "waiting-start"].includes(data.state)) timer = setTimeout(refresh, 700);
    if (automatic && !acting && data.supported && ["available", "ready"].includes(data.state)) {
      queueMicrotask(() => action(data.state === "available" ? "download" : "install"));
    }
    if (data.state === "installing" && data.platform !== "android" && !handoff) {
      handoff = true;
      window.webkit?.messageHandlers?.heliostatUpdateQuit?.postMessage("quit");
      window.chrome?.webview?.postMessage("update-quit");
    }
    if (["error", "cancelled", "current", "success"].includes(data.state)) automatic = false;
  }
  async function refresh() {
    clearTimeout(timer);
    try { render(await get("update/status")); }
    catch (error) { render({...state, state:"error"}); }
  }
  async function action(name) {
    if (acting) return;
    acting = true;
    clearTimeout(timer);
    try {
      const payload = {};
      if (name === "install" && state?.supported) {
        const preferences = {};
        // Save the app's operation settings before exiting a WebView whose
        // loopback origin (port) changes on restart.
        if (typeof localStorage !== "undefined") {
          for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key?.startsWith("heliostat-operation-")) preferences[key] = localStorage.getItem(key);
          }
        }
        payload.preferences = preferences;
      }
      const data = await post(`update/${name}`, payload);
      acting = false;
      render(data);
    } catch (error) {
      automatic = false;
      render({...state, state:"error", error:error.message || "更新操作失败，请重试。"});
    } finally { acting = false; }
  }
  async function open() { automatic = true; $("updatesDialog").showModal(); await refresh(); if (state?.supported && ["idle", "current", "error", "success", "cancelled"].includes(state.state)) { automatic = true; await action("check"); } }
  $("checkUpdates").onclick = () => { automatic = true; return action("check"); };
  $("cancelUpdate").onclick = () => { automatic = false; return action("cancel"); };
  $("downloadUpdate").onclick = () => action("download");
  $("installUpdate").onclick = () => action("install");
  const close = () => { $("updatesDialog").close(); };
  $("closeUpdates").onclick = close;
  $("dismissUpdates").onclick = close;
  $("updatesDialog").onclose = () => { if (!["checking", "downloading", "verifying", "installing", "waiting-start"].includes(state?.state)) clearTimeout(timer); };
  return open;
}
