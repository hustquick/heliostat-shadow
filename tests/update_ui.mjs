import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../viewer/updates.js', import.meta.url));
const {initUpdates} = await import('data:text/javascript;base64,' + source.toString('base64'));
const elements = new Map();
globalThis.document = {getElementById: id => {
  if (!elements.has(id)) elements.set(id, {dataset:{}, showModal(){this.open=true;}, close(){this.open=false;this.onclose?.();}});
  return elements.get(id);
}};
const stored = new Map([
  ['heliostat-operation-gemasolar', '{"startDni":400,"startElevation":15,"stopDni":100,"stopElevation":5}'],
  ['unrelated', 'keep private']
]);
globalThis.localStorage = {length:stored.size,key:index=>[...stored.keys()][index],getItem:key=>stored.get(key)};
let poll;
globalThis.setTimeout = fn => { poll = fn; return 1; };
globalThis.clearTimeout = () => { poll = null; };
const settle = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
let quit = 0, phase = 'idle';
globalThis.window = {chrome: {webview: {postMessage(message){assert.equal(message,'update-quit');quit++;}}}};
const identity = {current:'1.0.3',build:20004,platform:'windows-x64',supported:true};
const data = () => ({...identity,state:phase,latest:phase==='idle' ? undefined : '1.0.4',date:'2026-10-05',notes:'<script>literal notes</script>'});
const actions = [];
const open = initUpdates(async()=>data(), async(path,payload)=>{
  if(path==='update/install') assert.deepEqual(payload.preferences,{'heliostat-operation-gemasolar':stored.get('heliostat-operation-gemasolar')});
  actions.push(path); phase = {'update/check':'available','update/download':'downloading','update/install':'verifying','update/cancel':'cancelled'}[path];
  return data();
});
assert.equal(elements.has('openUpdates'), false, 'Mobile retains no update button');
await open(); await settle();
assert.deepEqual(actions,['update/check','update/download'], 'Check automatically downloads authenticated compatible update');
assert.equal(elements.get('updateDate').textContent,'2026.10.05 发布');
assert.equal(elements.get('updateNotes').textContent,'<script>literal notes</script>');
assert.equal(quit,0);
phase='ready'; await poll(); await settle();
assert.deepEqual(actions,['update/check','update/download','update/install']);
assert.equal(quit,0,'Do not exit before helper accepts installation');
elements.get('closeUpdates').onclick();
assert.ok(poll,'Closing dialog does not break installation handoff');
phase='installing'; await poll(); await settle();
assert.equal(quit,1);
await poll(); assert.equal(quit,1,'Only one native quit message');
phase='success'; await poll();
assert.equal(elements.get('updatesTitle').textContent,'更新成功');
phase='idle'; await open(); await settle();
await elements.get('cancelUpdate').onclick(); await settle();
assert.equal(phase,'cancelled');
assert.equal(elements.get('updatesTitle').textContent,'更新已取消');
console.log('PASS automatic pipeline, signature-validation state, cancellation, hidden-dialog polling and single restart handoff');
