import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../viewer/app.js', import.meta.url), 'utf8');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    disabled: false, value: 'overview', checked: false,
    classList: {add() {}, remove() {}, toggle() {}},
    setAttribute(name, value) { this[name] = value; },
  });
  return elements.get(id);
};
let resolveRequest, rejectRequest, requests = 0;
const context = vm.createContext({
  $: element,
  document: {querySelector: () => element('submit')},
  updateDesignMethod() {}, showEfficiencyScale() {}, drawDetails() {}, status() {},
  loadEfficiencies() {
    requests++;
    return new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
  },
  animateCamera(fn) { fn(); }, applyViewPreference() {}, overview() {},
});
vm.runInContext(`let analysisController = {live:false}, playing = false, busy = false, efficiencyEnabled = false, towerColorEnabled = false,
  efficiencyData = null, frame = {daylight:true, local_time:'12:00'}, concentrating = true,
  meta = {tower_count:2, towers:[{name:'A'},{name:'B'}]},
  towerColors = ['red','blue'], viewPreference = 'overview';`, context);
const busyCode = source.slice(source.indexOf('function controlsBusy('), source.indexOf('async function loadTime('));
const colorsCode = source.slice(source.indexOf('$("efficiencyColor").onclick'), source.indexOf('async function selectTarget('));
const viewCode = source.slice(source.indexOf('$("viewMode").onchange'), source.indexOf('$("viewIso").onclick'));
vm.runInContext(busyCode + colorsCode + viewCode, context);
const run = code => vm.runInContext(code, context);

run('controlsBusy(true)');
for (const id of ['focus','overview','viewMode','viewIso','viewTop','efficiencyColor','towerColor'])
  assert.equal(element(id).disabled, false, id);
assert.equal(element('plant').disabled, true);
element('viewMode').value = 'focus';
element('viewMode').onchange();
assert.equal(run('viewPreference'), 'focus');
await element('efficiencyColor').onclick();
assert.equal(run('efficiencyEnabled'), true);
assert.equal(requests, 0, 'playback request should be reused');
run('controlsBusy(false); controlsBusy(true); controlsBusy(false)');
assert.equal(run('viewPreference'), 'focus');
assert.equal(run('efficiencyEnabled'), true);

// A display preference can change while an efficiency request is outstanding.
await element('efficiencyColor').onclick(); // disable
const pending = element('efficiencyColor').onclick(); // enable, request pending
assert.equal(requests, 1);
element('towerColor').onclick();
assert.equal(run('towerColorEnabled'), true);
assert.equal(run('efficiencyEnabled'), false);
rejectRequest(new Error('request failed'));
await pending;
assert.equal(run('towerColorEnabled'), true);
assert.equal(run('efficiencyEnabled'), false);

run('frame.daylight = false; concentrating = false');
await element('efficiencyColor').onclick();
assert.equal(run('efficiencyEnabled'), true, 'retain preference for next daylight frame');
assert.equal(requests, 1);
console.log('Playback controls remain interactive; view/color preferences survive frame updates and delayed errors.');

run("analysisController.live=true; controlsBusy(true); controlsBusy(false)");
assert.equal(element("mirror").disabled,false);
assert.equal(element("submit").disabled,false);
run("analysisController.live=false; controlsBusy(false)");
assert.equal(element("mirror").disabled,false);
assert.equal(element("submit").disabled,false);
console.log("Target controls remain stable throughout live mode and remain interactive.");

run("playing=true; controlsBusy(true); controlsBusy(false)");
assert.equal(element("mirror").disabled,false);
assert.equal(element("submit").disabled,false);
run("playing=false; controlsBusy(false)");
assert.equal(element("mirror").disabled,false);
assert.equal(element("submit").disabled,false);
console.log("Target controls remain stable during hourly playback and remain interactive.");
