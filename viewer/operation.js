// Strict AND thresholds with hysteresis; nighttime is always parked.
export const defaultOperationParameters = {startDni: 200, startElevation: 10, stopDni: 100, stopElevation: 5};
export function validateOperationParameters(p) {
  for (const key of Object.keys(defaultOperationParameters))
    if (!Number.isFinite(p[key])) throw new Error("启停参数必须为有限数值");
  if (p.startDni < 0 || p.stopDni < 0 || p.startElevation < 0 || p.stopElevation < 0 || p.startElevation > 90 || p.stopElevation > 90)
    throw new Error("DNI 不得小于 0，太阳高度角应在 0–90° 之间");
  if (p.startDni < p.stopDni || p.startElevation < p.stopElevation)
    throw new Error("启动临界值不得低于对应的停止临界值");
  return p;
}
export function nextOperationState(running, dni, elevation, p) {
  if (!Number.isFinite(dni) || !Number.isFinite(elevation) || elevation <= 0) return false;
  if (!running && dni > p.startDni && elevation > p.startElevation) return true;
  if (running && dni < p.stopDni && elevation < p.stopElevation) return false;
  return running;
}
export function parkedVertices(c, width, height) {
  // Clockwise from above: reflecting face normal points down (−U).
  return [[-1,1],[1,1],[1,-1],[-1,-1]].map(([x,y])=>[c[0]+x*width/2,c[1]+y*height/2,c[2]]);
}
