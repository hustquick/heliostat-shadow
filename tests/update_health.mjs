import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../viewer/app.js', import.meta.url), 'utf8');
const start = source.indexOf('async function confirmDesktopInitialization()');
const end = source.indexOf('\ninit().then', start);
const implementation = source.slice(start, end);
async function scenario({ready=true, finished=true, lost=false, health='ok', desktop=true, rejected=false, concurrent=false}={}) {
  let messages=[], calls=0;
  const context=vm.createContext({
    desktopInitializationFinished:finished,desktopInitializationConfirmed:false,desktopInitializationConfirming:false,desktopViewerReady:ready,
    window:desktop ? {chrome:{webview:{postMessage:m=>messages.push(m)}}} : {heliostatNative:{}},
    api:async()=>{calls++;if(rejected)throw new Error('service failed');return {status:health};},
    requestAnimationFrame:callback=>callback(),frame:{},target:{},meta:{},
    renderer:{getContext:()=>({isContextLost:()=>lost})},console:{error(){}}
  });
  vm.runInContext(implementation,context);
  const first=context.confirmDesktopInitialization();
  if(concurrent) await context.confirmDesktopInitialization();
  await first;
  await context.confirmDesktopInitialization();
  return {messages,calls};
}
assert.deepEqual((await scenario({ready:false})).messages,[],'Caught initial frame/target errors must not confirm startup');
assert.deepEqual((await scenario({finished:false})).messages,[]);
assert.deepEqual((await scenario({lost:true})).messages,[],'Failed GPU initialization must retain backup');
assert.deepEqual((await scenario({health:'failed'})).messages,[]);
assert.deepEqual((await scenario({rejected:true})).messages,[]);
const mobile=await scenario({desktop:false});assert.equal(mobile.calls,0,'Android/iOS do not call desktop update health API');
const success=await scenario({concurrent:true});assert.deepEqual(success.messages,['update-healthy']);assert.equal(success.calls,1);
const loadStart=source.indexOf('async function loadTime()');
const loadEnd=source.indexOf('function showEfficiencyScale()',loadStart);
const loadSource=source.slice(loadStart,loadEnd);
assert.ok(loadSource.indexOf('desktopViewerReady = true')>loadSource.indexOf('draw2D()'));
assert.ok(loadSource.indexOf('desktopViewerReady = true')<loadSource.indexOf('} catch (e)'));
console.log('PASS service, first frame/target, finished initialization, GPU checks, single confirmation and unchanged mobile path');
