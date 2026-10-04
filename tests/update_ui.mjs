import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../viewer/updates.js', import.meta.url));
const {initUpdates} = await import('data:text/javascript;base64,' + source.toString('base64'));
const elements = new Map();
globalThis.document = {getElementById: id => {
  if (!elements.has(id)) elements.set(id, {showModal(){this.open=true;}, close(){this.open=false;}});
  return elements.get(id);
}};
let quit = 0, phase = 'idle';
globalThis.window = {chrome: {webview: {postMessage(message){assert.equal(message,'update-quit');quit++;}}}};
const identity = {current:'1.0.3',build:20004,platform:'windows-x64',supported:true};
const actions = [];
const open = initUpdates(async()=>({...identity,state:phase}), async(path)=>{
  actions.push(path); phase = {'update/check':'available','update/download':'ready','update/install':'installing'}[path];
  return {...identity,state:phase,latest:'1.0.4',notes:'<script>literal notes</script>'};
});
assert.equal(elements.has('openUpdates'), false, 'No on-page or mobile update button');
await open();
assert.deepEqual(actions,['update/check']);
assert.equal(elements.get('downloadUpdate').hidden,false);
assert.equal(elements.get('installUpdate').hidden,true);
assert.equal(elements.get('updateNotes').textContent,'<script>literal notes</script>');
await elements.get('downloadUpdate').onclick();
assert.equal(elements.get('installUpdate').hidden,false);
assert.equal(quit,0);
await elements.get('installUpdate').onclick();
assert.equal(quit,1, 'Exit only after native installer accepted the request');
console.log('PASS desktop menu entry, verified-download-before-install and restart handoff');
