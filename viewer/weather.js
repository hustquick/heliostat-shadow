// Common Open-Meteo adapter used unchanged by all four WebViews.
const ENDPOINT = 'https://api.open-meteo.com/v1/forecast';
export const WEATHER_CACHE_MS = 5 * 60_000;
export const WEATHER_MAX_AGE_MS = 30 * 60_000;

export function parseWeather(data, now = Date.now()) {
  if (data?.error) throw new Error(data.reason || '气象服务返回错误');
  const series = data?.minutely_15, units = data?.minutely_15_units;
  if (!Array.isArray(series?.time) || units?.direct_normal_irradiance_instant !== 'W/m²' || units?.surface_pressure !== 'hPa' || units?.temperature_2m !== '°C')
    throw new Error('气象服务缺少 DNI、气温、气压或单位不匹配');
  let index = -1;
  for (let i = 0; i < series.time.length; i++) {
    const stamp = series.time[i] * 1000;
    if (Number.isFinite(stamp) && stamp <= now && (index < 0 || stamp > series.time[index] * 1000)) index = i;
  }
  if (index < 0 || now - series.time[index] * 1000 > WEATHER_MAX_AGE_MS) throw new Error('气象数据已过期或设备时钟与服务时间不一致');
  const dni = series.direct_normal_irradiance_instant?.[index];
  const temperature = series.temperature_2m?.[index], pressure = series.surface_pressure?.[index];
  const missing=[];
  if(!Number.isFinite(dni)||dni<0||dni>2000) missing.push('dni');
  if(!Number.isFinite(temperature)||temperature < -90||temperature > 70) missing.push('temperature_c');
  if(!Number.isFinite(pressure)||pressure < 100||pressure > 1100) missing.push('pressure_hpa');
  if(missing.length) {
    const error=new Error('当前气象样本不完整或数值异常');
    error.missingFields=missing;
    throw error;
  }
  return {dni, temperature_c: temperature, pressure_hpa: pressure,
    weather_source:'open-meteo-model',weather_time:new Date(series.time[index]*1000).toISOString(),
    fetched_at:new Date(now).toISOString(), grid_latitude:data.latitude,grid_longitude:data.longitude};
}

export function createWeatherClient(fetcher = (...args) => fetch(...args), clock = () => Date.now()) {
  let cached = null;
  return async (site, signal) => {
    if (!Number.isFinite(site?.latitude) || !Number.isFinite(site?.longitude) || Math.abs(site.latitude)>90 || Math.abs(site.longitude)>180)
      throw new Error('当前电厂缺少有效经纬度，不能查询气象');
    const key = `${site.latitude},${site.longitude}`;
    const now = clock();
    if (cached?.key === key && now >= cached.fetched && now - cached.fetched < WEATHER_CACHE_MS && now >= Date.parse(cached.result.weather_time) && now - Date.parse(cached.result.weather_time) <= WEATHER_MAX_AGE_MS)
      return {...cached.result, cached:true};
    const params = new URLSearchParams({latitude:site.latitude,longitude:site.longitude,
      minutely_15:'direct_normal_irradiance_instant,temperature_2m,surface_pressure',
      past_minutely_15:'4',forecast_minutely_15:'4',timezone:'GMT',timeformat:'unixtime'});
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener('abort', abort, {once:true});
    const timeout = setTimeout(abort, 15_000);
    try {
      const response = await fetcher(`${ENDPOINT}?${params}`, {signal:controller.signal});
      if (!response.ok) throw new Error(`气象查询失败（HTTP ${response.status}）`);
      const result = parseWeather(await response.json(), clock());
      cached = {key, fetched:clock(),result};
      return {...result,cached:false};
    } finally { clearTimeout(timeout); signal?.removeEventListener('abort',abort); }
  };
}
