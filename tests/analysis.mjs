import assert from 'node:assert/strict';
import {adaptivePeriod,createSerialAnalysis,zonedInstant,localInput} from '../viewer/analysis.js';
import {parseWeather,createWeatherClient} from '../viewer/weather.js';
assert.equal(adaptivePeriod(90000),112500);
assert.equal(adaptivePeriod(200),60000);
assert.equal(zonedInstant('2026-10-07T12:34:56','Asia/Shanghai'),'2026-10-07T04:34:56.000Z');
assert.equal(localInput('2026-10-07T04:34:56Z','Asia/Shanghai'),'2026-10-07T12:34:56');
for(const s of ['2026-02-30T12:00','2026-03-08T02:30','2026-11-01T01:30']) assert.throws(()=>zonedInstant(s,'America/New_York'));
let now=1800000000000;
const sample=()=>({latitude:30,longitude:110,minutely_15_units:{direct_normal_irradiance_instant:'W/m²',temperature_2m:'°C',surface_pressure:'hPa'},minutely_15:{time:[now/1000-100,now/1000+800],direct_normal_irradiance_instant:[800,900],temperature_2m:[20,21],surface_pressure:[1000,1001]}});
assert.equal(parseWeather(sample(),now).dni,800);
for(const field of ['direct_normal_irradiance_instant','temperature_2m','surface_pressure']) {const data=sample();data.minutely_15[field][0]=null;assert.throws(()=>parseWeather(data,now));}
assert.throws(()=>parseWeather(sample(),now+2100000));
let calls=0;const weather=createWeatherClient(async()=>{calls++;return {ok:true,json:async()=>sample()};},()=>now);
const site={latitude:30,longitude:110};
assert.equal((await weather(site)).cached,false);assert.equal((await weather(site)).cached,true);assert.equal(calls,1);
await weather({...site,longitude:111});assert.equal(calls,2);
now+=301000;await weather(site);assert.equal(calls,3);
await assert.rejects(createWeatherClient(async()=>({ok:false,status:503}),()=>now)(site),/503/);
// A 90-second calculation is still the sole task; start-to-start period grows to 112.5s (22.5s rest).
let release,active=0,maxActive=0,results=[],timers=[];
const execute=async({instant})=>{active++;maxActive=Math.max(maxActive,active);await new Promise(r=>release=r);active--;return instant;};
const scheduler=createSerialAnalysis({execute,clock:()=>now,onResult:r=>results.push(r),setTimer:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimer:()=>{}});
const first=scheduler.start();assert.equal(scheduler.busy,true);assert.equal(timers.length,0);
now+=90000;release();await first;assert.equal(timers.at(-1).ms,22500);assert.equal(results.length,1);
const next=timers.pop().fn();assert.equal(active,1);
scheduler.stop();const restart=scheduler.start();await restart;assert.equal(active,1);release();await next;
assert.equal(results.length,1);assert.equal(maxActive,1);assert.equal(timers.at(-1).ms,0);
const resumed=timers.pop().fn();release();await resumed;assert.equal(results.length,2);
scheduler.stop();const one=scheduler.once();scheduler.stop();release();await one;assert.equal(results.length,2);
// Failed long tasks use the same adaptive retry, not immediate catch-up.
let failedDelay;
const failed=createSerialAnalysis({clock:()=>now,execute:async()=>{now+=90000;throw new Error('network');},setTimer:(fn,ms)=>{failedDelay=ms;return 1;},clearTimer:()=>{}});
await failed.start();assert.equal(failedDelay,22500);failed.stop();
// Device clock changes do not affect a monotonic duration measurement.
let monotonic=0,delay;
const adjusted=createSerialAnalysis({clock:()=>now,elapsedClock:()=>monotonic,execute:async()=>{now-=3600000;monotonic+=90000;return {};},setTimer:(fn,ms)=>{delay=ms;return 1;},clearTimer:()=>{}});
await adjusted.start();assert.equal(delay,22500);adjusted.stop();
// Android bridge returns asynchronously and propagates structured errors.
globalThis.window={heliostatNative:{requestAsync(id){queueMicrotask(()=>window.heliostatNativeComplete(id,'{"ok":true}'));}}};
const {androidRequest}=await import('../viewer/native-bridge.js');
assert.deepEqual(await androidRequest('POST','analysis/instant',{}),{ok:true});
window.heliostatNative.requestAsync=id=>queueMicrotask(()=>window.heliostatNativeComplete(id,'{"error":"broken"}'));
await assert.rejects(androidRequest('GET','meta',{}),/broken/);
console.log('analysis scheduler, slow computation, cancellation, timezones, weather validation/cache and asynchronous native bridge passed');
