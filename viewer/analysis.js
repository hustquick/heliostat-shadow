import {createWeatherClient} from './weather.js';

export const BASE_PERIOD_MS = 5_000;
export function adaptivePeriod(computeMs, base = BASE_PERIOD_MS) {
  return (Math.floor(Math.max(0, computeMs) / base) + 1) * base;
}

// Convert a plant-local wall clock without borrowing the device timezone.
// Reject DST gaps and ambiguity instead of silently choosing another instant.
export function zonedInstant(local, timezone) {
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(local)) throw new Error('请选择完整日期和时刻');
  const [year,month,day,hour,minute,second=0] = local.split(/[-T:]/).map(Number);
  if (year<1900 || year>2100 || month<1 || month>12 || day<1 || day>31 || hour>23 || minute>59 || second>59) throw new Error('日期或时刻无效');
  const naive = Date.UTC(year,month-1,day,hour,minute,second);
  const formatter = new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  const parts = stamp => Object.fromEntries(formatter.formatToParts(new Date(stamp)).map(p=>[p.type,p.value]));
  const matches = stamp => {
    const p=parts(stamp);
    return Number(p.year)===year && Number(p.month)===month && Number(p.day)===day && Number(p.hour)===hour && Number(p.minute)===minute && Number(p.second)===second;
  };
  const offsets = new Set();
  for (const shift of [-36,-12,0,12,36]) {
    const stamp=naive+shift*3600_000,p=parts(stamp);
    offsets.add(Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second)-stamp);
  }
  const candidates=[...offsets].map(o=>naive-o).filter(matches);
  if(candidates.length!==1) throw new Error(candidates.length ? '此当地时间因夏令时重复，请选择其他时刻' : '此日期或当地时间不存在');
  return new Date(candidates[0]).toISOString();
}

export function localInput(instant, timezone) {
  const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(instant)).map(p=>[p.type,p.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}

export function createSerialAnalysis({execute,onResult,onStatus,clock=()=>Date.now(),setTimer=setTimeout,clearTimer=clearTimeout,base=BASE_PERIOD_MS,elapsedClock=clock}) {
  let running=false,busy=false,timer=null,generation=0,controller=null;
  const cancel = () => { const active=running||busy;generation++;running=false;clearTimer(timer);controller?.abort();if(active)onStatus?.({state:'stopped'}); };
  const run = async live => {
    if(busy) return false;
    const version=generation;busy=true;controller=new AbortController();
    const started=clock(),startedElapsed=elapsedClock();onStatus?.({state:'running',live});
    try {
      const result=await execute({live,instant:new Date(started).toISOString(),signal:controller.signal});
      if(version!==generation) return false;
      const elapsed=Math.max(0,elapsedClock()-startedElapsed),period=adaptivePeriod(elapsed,base);
      onResult?.(result,{elapsed,period,live});
      onStatus?.({state:'ready',elapsed,period,nextAt:live?clock()+Math.max(0,period-elapsed):null,live});
      if(live&&running) timer=setTimer(()=>run(true),Math.max(0,period-elapsed));
      return true;
    } catch(error) {
      if(version===generation) {
        onStatus?.({state:'error',error:error.message,live});
        if(live&&running) timer=setTimer(()=>run(true),Math.max(0,adaptivePeriod(elapsedClock()-startedElapsed,base)-(elapsedClock()-startedElapsed)));
      }
      return false;
    } finally {
      busy=false;controller=null;
      onStatus?.({state:'drained'});
      // Stop/start while an old native calculation drains: start only after
      // it returns, and never apply or catch up its obsolete result.
      if(version!==generation&&running) timer=setTimer(()=>run(true),0);
    }
  };
  return {start(){cancel();running=true;return run(true);},stop:cancel,once(){cancel();return run(false);},get busy(){return busy;},get running(){return running;}};
}

export async function waitForIdle(isBusy, signal) {
  while (isBusy()) {
    if (signal.aborted) throw new Error('分析已停止');
    await new Promise((resolve,reject)=>{
      let timer;
      const finish=()=>{signal.removeEventListener('abort',abort);resolve();};
      const abort=()=>{clearTimeout(timer);signal.removeEventListener('abort',abort);reject(new Error('分析已停止'));};
      signal.addEventListener('abort',abort,{once:true});
      timer=setTimeout(finish,50);
    });
  }
  if(signal.aborted) throw new Error('分析已停止');
}

export function initAnalysis({getContext,post,applyResult,weather=createWeatherClient(),onModeChange=()=>{},onStart=()=>{},setBusy=()=>{}}) {
  const $=id=>document.getElementById(id);
  let lastResult=null,stepping=false;
  const runner=createSerialAnalysis({
    elapsedClock:()=>performance.now(),
    execute:async ({live,signal})=>{
      let ctx=getContext();
      if(!ctx?.meta) throw new Error('镜场尚未加载');
      if(ctx.busy) {
        $('analysisStatus').textContent='等待当前镜场计算完成…';
        await waitForIdle(()=>getContext().busy,signal);
        ctx=getContext();
      }
      onModeChange(live?'live':stepping?'playback':'instant');
      const time=live?new Date().toISOString():zonedInstant($('analysisTime').value,ctx.meta.timezone);
      const number=id=>{if(!$(id).value.trim())throw new Error('请完整填写气象参数');return Number($(id).value);};
      setBusy(true);
      try {
      const meteo=live?await weather(ctx.meta.site_location,signal):{
        dni:number('analysisDni'),temperature_c:number('analysisTemperature'),pressure_hpa:number('analysisPressure'),weather_source:'manual',weather_time:time};
      if(signal.aborted) throw new Error('分析已停止');
      const result=await post('analysis/instant',{time,mirror:ctx.selected,...meteo});
      if(signal.aborted) throw new Error('分析已停止');
      if(result.plant_id!==ctx.meta.active_plant) throw new Error('镜场已切换，此结果已丢弃');
      return {result,meteo};
      } finally {setBusy(false);}
    },
    onResult:({result})=>{lastResult=result;applyResult(result);},
    onStatus:state=>{
      $('stopAnalysis').disabled=!runner?.running&&!runner?.busy;
      $('runAnalysis').disabled=state.state==='running'||runner?.busy;$('liveAnalysis').disabled=state.state==='running'||runner?.busy;
      if(state.state==='running') $('analysisStatus').textContent=state.live?'正在查询气象并分析设备当前时刻…':'正在计算指定时刻效率…';
      else if(state.state==='error') {$('analysisStatus').textContent=`分析失败：${state.error}${state.live?'；稍后重试，保留上次结果。':''}`;$('analysisStatus').dataset.stale='true';}
      else if(state.state==='ready') {$('analysisStatus').textContent=`计算耗时 ${(state.elapsed/1000).toFixed(1)} 秒${state.live?` · 更新周期 ${(state.period/1000).toFixed(0)} 秒 · 下次计算 ${new Date(state.nextAt).toLocaleTimeString('zh-CN',{hour12:false})}`:''}`;$('analysisStatus').dataset.stale='false';}
      else if(state.state==='stopped') $('analysisStatus').textContent='已停止分析；已完成的结果保留。';
    }
  });
  $('runAnalysis').onclick=()=>{onStart('instant');runner.once();};$('liveAnalysis').onclick=()=>{onStart('live');runner.start();};$('stopAnalysis').onclick=()=>runner.stop();
  document.addEventListener('visibilitychange',()=>{if(document.hidden)runner.stop();});
  const advance=async delta=>{
    if(runner.busy||getContext().busy) return false;
    if(delta) {
      try {
        const time=zonedInstant($('analysisTime').value,getContext().meta.timezone);
        const shifted=new Date(Date.parse(time)+delta*3600000).toISOString();
        if(new Date(shifted).getUTCFullYear()<1900||new Date(shifted).getUTCFullYear()>2100) throw new Error('日期超出支持范围');
        $('analysisTime').value=localInput(shifted,getContext().meta.timezone);
      } catch(error) { $('analysisStatus').textContent=error.message;return false; }
    }
    stepping=true;
    try{return await runner.once();}finally{stepping=false;}
  };
  return {step:advance,focus:onStart,stop:()=>runner.stop(),get busy(){return runner.busy;},get live(){return runner.running;},get result(){return lastResult;},initialize(){const ctx=getContext();$('analysisTimezone').textContent=ctx.meta.timezone;$('analysisTime').value=localInput(ctx.currentTime||Date.now(),ctx.meta.timezone);$('analysisTemperature').value=ctx.frame?.temperature??12;}};
}
