// Android JavascriptInterface cannot return a Promise; deliver native results
// asynchronously, keeping WebView responsive during Rust calculations.
const pending = new Map();
let sequence = 0;
export function androidRequest(method, path, payload) {
  return new Promise((resolve, reject) => {
    const id = String(++sequence);
    pending.set(id, {resolve, reject});
    try { window.heliostatNative.requestAsync(id, method, path, JSON.stringify(payload)); }
    catch (error) { pending.delete(id); reject(error); }
  });
}
if (typeof window !== 'undefined') window.heliostatNativeComplete = (id, raw) => {
  const task = pending.get(String(id));
  if (!task) return;
  pending.delete(String(id));
  try {
    const data = JSON.parse(raw);
    if (data?.error) throw new Error(data.error);
    task.resolve(data);
  } catch (error) { task.reject(error); }
};
