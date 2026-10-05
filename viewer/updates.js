export function initUpdates(get, post) {
  const $ = id => document.getElementById(id);
  let state, timer;
  function render(data) {
    state = data;
    $("updatesDialog").dataset.state = data.state;
    const hasNewVersion = data.latest && data.latest !== data.current && ["available", "downloading", "ready", "installing", "error"].includes(data.state);
    $("updateVersion").textContent = hasNewVersion ? `${data.current} → ${data.latest}` : `版本 ${data.current || "—"}`;
    const titles = {idle:"软件更新", checking:"正在检查更新", available:"发现新版本", current:"已是最新版本", downloading:"正在下载更新", ready:"更新已就绪", installing:"正在安装更新", error:"暂时无法更新"};
    $("updatesTitle").textContent = titles[data.state] || "软件更新";
    $("updateStatusMark").textContent = {current:"✓", ready:"✓", available:"↓", downloading:"↓", error:"!"}[data.state] || "↻";
    const messages = {idle:"获取最新版本和改进说明。", checking:"", available:"安装后将重新启动应用。", current:"", downloading:`已下载 ${data.progress || 0}%`, ready:"新版已准备好，安装后将重新启动应用。", installing:"应用即将重新启动。", error:"请检查网络连接后重试。"};
    $("updateMessage").textContent = messages[data.state] || "";
    $("updateMessage").hidden = !$("updateMessage").textContent;
    if (!data.supported) { $("updateMessage").hidden = false; $("updateMessage").textContent = "请使用完整桌面版进行更新。"; }
    const showRelease = ["available", "downloading", "ready", "installing"].includes(data.state) || (data.state === "current" && data.latest === data.current);
    $("updateNotesPanel").hidden = !showRelease || !data.notes?.trim();
    $("updateNotes").textContent = showRelease ? data.notes || "" : "";
    const date = /^\d{4}-\d{2}-\d{2}$/.test(data.date || "") ? data.date : "";
    $("updateDate").hidden = !showRelease || !date;
    $("updateDate").textContent = date ? `${date.replaceAll("-", ".")} 发布` : "";
    $("checkUpdates").hidden = !["idle", "error"].includes(data.state) || Boolean(hasNewVersion);
    $("checkUpdates").textContent = data.state === "error" ? "重试" : "检查更新";
    $("downloadUpdate").hidden = !data.supported || !["available", "error"].includes(data.state) || !hasNewVersion;
    $("downloadUpdate").textContent = data.state === "error" ? "重新下载" : "下载更新";
    $("installUpdate").hidden = data.state !== "ready";
    $("dismissUpdates").textContent = ["available", "ready"].includes(data.state) ? "稍后" : "完成";
    $("updateProgress").hidden = data.state !== "downloading";
    $("updateProgress").value = data.progress || 0;
    if (["checking", "downloading"].includes(data.state)) timer = setTimeout(refresh, 700);
  }
  async function refresh() {
    clearTimeout(timer);
    try { render(await get("update/status")); }
    catch (error) { render({...state, state:"error"}); }
  }
  async function action(name) {
    clearTimeout(timer);
    try {
      render(await post(`update/${name}`, {}));
      if (name === "install" && state.state === "installing" && state.platform !== "android") {
        window.webkit?.messageHandlers?.heliostatUpdateQuit?.postMessage("quit");
        window.chrome?.webview?.postMessage("update-quit");
      }
    } catch (error) { $("updateMessage").hidden = false;
      $("updateMessage").textContent = name === "install" ? "暂时无法安装，请确认应用目录可写后重试。" : "暂时无法获取更新，请检查网络后重试。"; }
  }
  async function open() { $("updatesDialog").showModal(); await refresh(); if (state?.supported && ["idle", "current", "error"].includes(state.state)) await action("check"); }
  $("checkUpdates").onclick = () => action("check");
  $("downloadUpdate").onclick = () => action("download");
  $("installUpdate").onclick = () => action("install");
  const close = () => { clearTimeout(timer); $("updatesDialog").close(); };
  $("closeUpdates").onclick = close;
  $("dismissUpdates").onclick = close;
  $("updatesDialog").onclose = () => clearTimeout(timer);
  return open;
}
