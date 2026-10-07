import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../viewer/app.js',import.meta.url),'utf8');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',open:false});return elements.get(id);};
for(const [id,value] of Object.entries({analysisTime:'2025-03-20T14:00',analysisDni:'800',analysisTemperature:'20',analysisPressure:'1013'})) $(id).value=value;
let live=false,stepOk=true,reloaded=false,resumes=[],steps=[];
const saved=new Map();
const context=vm.createContext({$,URLSearchParams,location:{hash:"",pathname:"/",search:"",reload:()=>{reloaded=true;}},history:{replaceState(_a,_b,url){context.location.hash=url.slice(url.indexOf("#"));}},meta:{timezone:"UTC",timestamps:[],default_time:"2025-03-20T00:00:00Z"},historicalSample(){},zonedInstant:v=>v,localInput:v=>v,sessionStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},
 analysisController:{get live(){return live;},stop(){live=false;},resume:v=>resumes.push(v)},
 stopPlay:()=>vm.runInContext('playing=false',context),controlsBusy:v=>vm.runInContext(`busy=${v}`,context),
 waitForIdle:async()=>{},AbortController,status(){},apiPost:async()=>{},reloadLayoutInTopView:()=>{reloaded=true;},
 step:async d=>{steps.push(d);return stepOk;},setTimeout:()=>1,playLoop(){}});
vm.runInContext('let playing=false,busy=false,analysisSnapshot=null,playTimer,selectedAnalysisMode="historical";',context);
vm.runInContext(source.slice(source.indexOf('const analysisTransferKey'),source.indexOf('$("plant").onchange')),context);
const run=s=>vm.runInContext(s,context);
for(const mode of ['live','playback','instant']) {
 live=mode==='live';run(`playing=${mode==='playback'};analysisSnapshot=${mode==='instant'?'{}':'null'};switchingLayout=false;selectedAnalysisMode="${mode}";`);
 await run('selectLayout("new-plant")');assert.equal(reloaded,true);
 const record=JSON.parse(saved.get('heliostat-analysis-transfer'));assert.equal(record.mode,mode);
 $('analysisTime').value='other';
 await run('restoreAnalysisMode()');assert.equal(saved.size,0);assert.equal($('analysisTime').value,record.time);
 if(mode==='playback')assert.equal(run('playing'),true);else assert.equal(resumes.at(-1),mode==='live');
}
stepOk=false;await run('restoreAnalysisMode({mode:"playback",time:"outside"})');
assert.equal(run('playing'),false);assert.equal($('analysisPanel').open,true);
console.log('Plant switches transfer live, hourly and specified-time modes and retain unavailable-sample feedback.');

// WebView reload must restore mode even when session storage is unavailable.
saved.clear();context.location.hash='#layout-field-top&analysis='+encodeURIComponent(JSON.stringify({mode:'live',time:'2025-03-20T14:00',dni:'800',temperature:'20',pressure:'1013'}));
await run('restoreAnalysisMode()');assert.equal(resumes.at(-1),true);
assert.equal(run('selectedAnalysisMode'),'live');
console.log('Navigation-carried mode restores without session storage.');
