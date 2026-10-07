import {androidRequest} from './native-bridge.js';
import {initUpdates} from "./updates.js";
import {initAnalysis,zonedInstant,localInput,historicalSample,waitForIdle} from "./analysis.js";

import {defaultOperationParameters, validateOperationParameters, nextOperationState, parkedVertices} from "./operation.js";
import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import {fieldColors as colors} from "./palette.js";
let analysisController, analysisSnapshot = null;
let selectedAnalysisMode="historical";
let desktopInitializationFinished = false, desktopInitializationConfirmed = false, desktopViewerReady = false, desktopInitializationConfirming = false;
const $ = (id) => document.getElementById(id);
const mobileClient = Boolean(window.heliostatNative?.requestAsync || window.webkit?.messageHandlers?.heliostatNative);
document.documentElement.classList.toggle("mobile-client", mobileClient);
for (const swatch of document.querySelectorAll("[data-loss-color]"))
  swatch.style.backgroundColor = colors[swatch.dataset.lossColor];

let meta,
  frame,
  target,
  mode = "joint",
  selected = "G261",
  busy = false,
  playing = false,
  playTimer,
  mesh,
  nightPoints,
  hovered = null,
  efficiencyEnabled = false,
  towerColorEnabled = false,
  efficiencyData = null,
  fieldEfficiencyData = null,
  viewPreference = "overview",
  cameraFramed = false,
  mirrorIndex = new Map();
let timesByDate = new Map(),
  currentTime = "",
  generation = 0;
const poseOverrides = new Map();
let patternFontScale = 1;
let concentrating = false, operationParameters = {...defaultOperationParameters};
const V = (p) => new THREE.Vector3(...p);
const efficiencyLowColor = new THREE.Color("#2f6fdb"),
  efficiencyMidColor = new THREE.Color("#f2c96d"),
  efficiencyHighColor = new THREE.Color("#e34b3d");
const towerColors = ["#2f88d8", "#b15bd6", "#31a77a", "#e08b32"];
const scene = new THREE.Scene();
scene.background = new THREE.Color("#10212b");
const camera = new THREE.PerspectiveCamera(45, 1, 0.05, 10000);
camera.up.set(0, 0, 1);
let renderer;
let webglUnavailable = false;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
} catch (e) {
  // A few Android emulators start without a usable GPU renderer.  Keep the
  // native field API and the 2D projection usable instead of aborting the
  // whole application before the plant list is populated.
  webglUnavailable = true;
  const canvas = document.createElement("canvas");
  renderer = {
    domElement: canvas,
    setPixelRatio() {},
    setSize(width, height) { canvas.width = width; canvas.height = height; },
    setAnimationLoop() {},
    render() {},
  };
}
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
$("scene").append(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxDistance = 4500;
controls.minDistance = 3;
let cameraTransition = null;
const details = new THREE.Group();
scene.add(details);
scene.add(new THREE.AxesHelper(90));
const raycaster = new THREE.Raycaster(),
  pointer = new THREE.Vector2();
function frameVisible(direction, up) {
  // Read the live viewport: its height can change before ResizeObserver runs.
  const viewport = $("scene").getBoundingClientRect();
  if (viewport.width > 0 && viewport.height > 0)
    camera.aspect = viewport.width / viewport.height;
  camera.up.copy(up);
  const points = [];
  if (frame?.daylight) {
    const allowed = relevant().candidates;
    meta.mirror_ids.forEach((id, i) => {
      if (!$("neighbors").checked || id === selected || allowed.has(id))
        points.push(...displayVertices(i).map(V));
    });
  } else if (frame?.centres) points.push(...frame.centres.map(V));
  if (!points.length) points.push(new THREE.Vector3(-10,-10,0), new THREE.Vector3(10,10,0));
  const box = new THREE.Box3().setFromPoints(points);
  const centre = box.getCenter(new THREE.Vector3());
  const forward = direction.clone().normalize();
  const right = up.clone().cross(forward).normalize();
  const vertical = forward.clone().cross(right).normalize();
  const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const tanH = tanV * Math.max(0.1, camera.aspect);
  let distance = 3;
  for (const p of points) {
    const q = p.clone().sub(centre), depth = q.dot(forward);
    distance = Math.max(distance, depth + Math.abs(q.dot(right))/tanH,
      depth + Math.abs(q.dot(vertical))/tanV);
  }
  const fittedDistance = distance * 1.15;
  // OrbitControls must not clamp the fitted distance on narrow screens.
  controls.maxDistance = Math.max(4500, fittedDistance * 2);
  camera.far = Math.max(10000, fittedDistance * 4);
  camera.updateProjectionMatrix();
  camera.position.copy(centre).addScaledVector(forward, fittedDistance);
  controls.target.copy(centre);
  controls.update();
}
function overview() { frameVisible(new THREE.Vector3(1,-1.05,0.92), new THREE.Vector3(0,0,1)); }
function topView() { frameVisible(new THREE.Vector3(0,0,1), new THREE.Vector3(0,1,0)); }
function focus() {
  if (!target?.daylight || !concentrating) return;
  const c = V(target.centre);
  camera.position
    .copy(c)
    .add(V(target.normal).multiplyScalar(40))
    .add(new THREE.Vector3(22, -28, 15));
  controls.target.copy(c);
  controls.update();
}
function focusIsometric() {
  if (!target?.daylight) return overview();
  const c = V(target.centre);
  camera.up.set(0, 0, 1);
  camera.position.copy(c).add(new THREE.Vector3(1, -1.05, 0.92).normalize().multiplyScalar(65));
  controls.target.copy(c);
  controls.update();
}
function focusTopView() {
  if (!target?.daylight) return topView();
  const c = V(target.centre);
  camera.up.set(0, 1, 0);
  camera.position.set(c.x, c.y, c.z + 65);
  controls.target.copy(c);
  controls.update();
}
function animateCamera(change) {
  const fromPosition=camera.position.clone(),fromTarget=controls.target.clone(),fromUp=camera.up.clone();
  change();
  if(window.matchMedia("(prefers-reduced-motion: reduce)").matches)return;
  cameraTransition={fromPosition,fromTarget,fromUp,toPosition:camera.position.clone(),toTarget:controls.target.clone(),toUp:camera.up.clone(),start:performance.now()};
  camera.position.copy(fromPosition);controls.target.copy(fromTarget);camera.up.copy(fromUp);controls.update();
}
controls.addEventListener("start",()=>{cameraTransition=null;});
function applyViewPreference() {
  if (viewPreference === "focus" && target?.daylight) focus();
}
function resize() {
  const { width, height } = $("scene").getBoundingClientRect();
  renderer.setSize(width, height);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe($("scene"));
renderer.setAnimationLoop(() => {
  if (cameraTransition) {
    const t=Math.min(1,(performance.now()-cameraTransition.start)/450),e=t*t*(3-2*t);
    camera.position.lerpVectors(cameraTransition.fromPosition,cameraTransition.toPosition,e);
    controls.target.lerpVectors(cameraTransition.fromTarget,cameraTransition.toTarget,e);
    camera.up.lerpVectors(cameraTransition.fromUp,cameraTransition.toUp,e).normalize();
    if(t===1)cameraTransition=null;
  }
  controls.update();
  renderer.render(scene, camera);
});
overview();
function disposeGroup(group) {
  for (const o of [...group.children]) {
    group.remove(o);
    o.traverse((x) => {
      x.geometry?.dispose();
      if (x.material) {
        for (const m of [].concat(x.material)) m.dispose();
      }
    });
  }
}
function removeObject(o) {
  if (o) {
    scene.remove(o);
    o.geometry?.dispose();
    o.material?.dispose();
  }
}
function line(points, color) {
  const o = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(points.map(V)),
    new THREE.LineBasicMaterial({ color }),
  );
  details.add(o);
  return o;
}
function thickOutline(points, color, radius = 0.15) {
  const vertices = points.map(V);
  if (vertices.length > 1 && vertices[0].distanceTo(vertices[vertices.length - 1]) < 1e-6)
    vertices.pop();
  const up = new THREE.Vector3(0, 1, 0);
  vertices.forEach((a, i) => {
    const b = vertices[(i + 1) % vertices.length],
      direction = b.clone().sub(a),
      length = direction.length(),
      edge = new THREE.Mesh(
        new THREE.CylinderGeometry(radius, radius, length, 10),
        new THREE.MeshBasicMaterial({color}),
      );
    edge.position.copy(a).add(b).multiplyScalar(0.5);
    edge.quaternion.setFromUnitVectors(up, direction.normalize());
    details.add(edge);
    const corner = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 10, 8),
      new THREE.MeshBasicMaterial({color}),
    );
    corner.position.copy(a);
    details.add(corner);
  });
}
function arrow(origin, dir, len, color) {
  details.add(
    new THREE.ArrowHelper(
      V(dir),
      V(origin),
      len,
      color,
      Math.min(4, len * 0.18),
      Math.min(1.5, len * 0.07),
    ),
  );
}
function world(xy, basis = target.views.mirror.basis, offset = 0.04) {
  return V(target.centre)
    .addScaledVector(V(basis[0]), xy[0])
    .addScaledVector(V(basis[1]), xy[1])
    .addScaledVector(V(target.normal), offset);
}
function addMask(parts, color, offset = 0.04) {
  for (const p of parts) {
    const s = new THREE.Shape(p.outer.map((x) => new THREE.Vector2(...x)));
    s.holes = p.holes.map(
      (h) => new THREE.Path(h.map((x) => new THREE.Vector2(...x))),
    );
    const g = new THREE.ShapeGeometry(s),
      pos = g.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const v = world([pos.getX(i), pos.getY(i)], undefined, offset);
      pos.setXYZ(i, v.x, v.y, v.z);
    }
    g.computeVertexNormals();
    details.add(
      new THREE.Mesh(
        g,
        new THREE.MeshBasicMaterial({
          color,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      ),
    );
  }
}
function drawDetails() {
  disposeGroup(details);
  if (!concentrating) { colorMirrors(); return; }
  if (!target?.daylight) return;
  if (poseOverrides.size) { colorMirrors(); return; }
  const p = target.views.mirror.polygons;
  if (!(efficiencyEnabled && efficiencyData) && !towerColorEnabled) {
    addMask(p.visible, colors.visible);
    if (mode === "joint") {
      addMask(p.shadow_only, colors.shadow);
      addMask(p.blocking_only, colors.blocking);
      addMask(p.overlap, colors.overlap);
    } else {
      addMask(p.target, colors.visible);
      addMask(p[mode], colors[mode], 0.055);
    }
  }
  for (const ring of p.target) {
    const outline = ring.outer.map((x) => world(x, undefined, 0.12).toArray());
    if ((efficiencyEnabled && efficiencyData) || towerColorEnabled) thickOutline(outline, "#32d274");
    else line(outline, colors.target);
  }
  const c = target.centre,
    s = frame.sun;
  arrow(
    V(c).addScaledVector(V(s), 30).toArray(),
    s.map((v) => -v),
    30,
    colors.incoming,
  );
  arrow(c, target.normal, 18, "#80df86");
  const r = V(target.aim).sub(V(c));
  arrow(c, target.reflected, r.length(), colors.reflected);
  const ball = new THREE.Mesh(
    new THREE.SphereGeometry(0.65, 8, 8),
    new THREE.MeshBasicMaterial({ color: colors.target }),
  );
  ball.position.copy(V(target.aim));
  details.add(ball);
  colorMirrors();
}
function relevant() {
  const modes = mode === "joint" ? ["shadow", "blocking"] : [mode],
    candidates = new Set(),
    shadow = new Set(),
    blocking = new Set();
  if (target?.daylight && concentrating) {
    for (const m of modes) {
      target.modes[m].occluder_ids.forEach((id) => candidates.add(id));
      target.modes[m].occluder_ids.forEach((id) =>
        (m === "shadow" ? shadow : blocking).add(id),
      );
    }
  }
  return { candidates, shadow, blocking };
}
function colorMirrors() {
  if (!mesh || !frame) return;
  const sets = relevant(),
    matrix = new THREE.Matrix4(),
    color = new THREE.Color();
  meta.mirror_ids.forEach((id, i) => {
    const vs = displayVertices(i),
      a = V(vs[0]),
      u = V(vs[1]).sub(a),
      v = V(vs[3]).sub(a),
      n = u.clone().cross(v).normalize(),
      c = a.clone().addScaledVector(u, 0.5).addScaledVector(v, 0.5);
    matrix.makeBasis(u, v, n);
    matrix.setPosition(c);
    if ($("neighbors").checked && id !== selected && !sets.candidates.has(id))
      matrix.scale(new THREE.Vector3(0, 0, 0));
    mesh.setMatrixAt(i, matrix);
    if (efficiencyEnabled && efficiencyData) {
      const eta = efficiencyData.values[i],
        span = Math.max(1e-12, efficiencyData.maximum - efficiencyData.minimum),
        scaled = (eta - efficiencyData.minimum) / span;
      if (scaled <= 0.5)
        color.lerpColors(efficiencyLowColor, efficiencyMidColor, scaled * 2);
      else
        color.lerpColors(efficiencyMidColor, efficiencyHighColor, (scaled - 0.5) * 2);
    } else if (towerColorEnabled && frame.tower_ids) {
      const towerIndex = meta.towers.findIndex((tower) => tower.id === frame.tower_ids[i]);
      color.set(towerColors[Math.max(0, towerIndex) % towerColors.length]);
    } else {
      color.set(colors.body);
      if (sets.candidates.has(id)) color.set(colors.candidate);
      if (sets.shadow.has(id)) color.set(colors.shadow);
      if (sets.blocking.has(id)) color.set(colors.blocking);
      if (sets.shadow.has(id) && sets.blocking.has(id)) color.set(colors.overlap);
      if (id === selected) color.set(colors.target);
    }
    if (id === hovered) color.set("#ffffff");
    // Pattern mirrors keep their dedicated gray in every coloring mode.
    if (!concentrating) color.set("#87949e");
    else if (poseOverrides.has(id)) color.set("#c4c9ce");
    else if (poseOverrides.size && !(efficiencyEnabled && efficiencyData) && !towerColorEnabled)
      color.set("#315264");
    mesh.setColorAt(i, color);
  });
  mesh.instanceMatrix.needsUpdate = true;
  mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere();
}
function drawFrame() {
  removeObject(mesh);
  removeObject(nightPoints);
  mesh = null;
  nightPoints = null;
  disposeGroup(details);
  $("night").hidden = true;
  if (frame) {
    mesh = new THREE.InstancedMesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }),
      meta.mirror_ids.length,
    );
    scene.add(mesh);
    colorMirrors();
  } else {
    nightPoints = new THREE.Points(
      new THREE.BufferGeometry().setFromPoints(frame.centres.map(V)),
      new THREE.PointsMaterial({ color: "#607581", size: 3 }),
    );
    scene.add(nightPoints);
  }
  // Fit only the first frame. Later time steps keep the user's orbit, pan and zoom.
  if (!cameraFramed) {
    overview();
    cameraFramed = true;
  }
  $("displayTime").dateTime=frame.timestamp;
  $("displayTime").textContent=new Date(frame.timestamp).toLocaleString("zh-CN",{timeZone:meta.timezone,hour12:false})+" · "+meta.timezone;
  for(const id of ["weatherDni","weatherElevation","weatherTemperature"]) $(id).removeAttribute("data-missing");
  $("weatherDni").textContent = frame.dni.toFixed(1);
  $("weatherElevation").textContent = frame.elevation.toFixed(2);
  $("weatherTemperature").textContent = Number.isFinite(frame.temperature) ? frame.temperature.toFixed(1) : "数据缺失";
}
function svgPath(parts) {
  return parts
    .map((p) =>
      [p.outer, ...p.holes]
        .map((r) => "M" + r.map((x) => `${x[0]},${-x[1]}`).join("L") + "Z")
        .join(""),
    )
    .join("");
}
function svgElement(name, attrs) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function insideRing(p, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i],
      b = ring[j];
    if (
      a[1] > p[1] !== b[1] > p[1] &&
      p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]
    )
      inside = !inside;
  }
  return inside;
}
function insideParts(p, parts) {
  return parts.some(
    (poly) =>
      insideRing(p, poly.outer) && !poly.holes.some((h) => insideRing(p, h)),
  );
}
const sceneDescription = document.querySelector(".scene-description");
function sizeSceneArea() {
  const card = sceneDescription.closest("details");
  if (!card.open) return;
  const height = sceneDescription.getBoundingClientRect().height;
  if (height <= 0) return;
  card.style.setProperty("--scene-notes-space", `${height + 32}px`);
  const scene = $("scene");
  const available = card.clientHeight - scene.offsetTop - height - 32;
  if (available > 0 && Math.abs(scene.getBoundingClientRect().height - available) > 1)
    scene.style.height = `${available}px`;
}
const sceneAreaObserver = new ResizeObserver(sizeSceneArea);
sceneAreaObserver.observe(sceneDescription);
sceneAreaObserver.observe(sceneDescription.closest("details"));
sceneDescription.closest("details").addEventListener("toggle", sizeSceneArea);

function alignPlantSelectorWidth() {
  const card = document.querySelector("main > .spatial");
  if (!card) return;
  const {width, left} = card.getBoundingClientRect();
  document.documentElement.style.setProperty("--primary-card-width", `${width}px`);
  $("plant").parentElement.style.maxWidth = `${width}px`;
  for (const selector of [".controlarea"]) {
    const row = document.querySelector(selector);
    row.style.paddingLeft = `${left}px`; row.style.paddingRight = `${left}px`;
  }
}
new ResizeObserver(alignPlantSelectorWidth).observe(document.querySelector("main > .spatial"));
alignPlantSelectorWidth();

function fitProjectionPlaceholder() {
  const svg = $("projection"), label = svg.querySelector(".empty-projection-label");
  if (!label) return;
  const {width, height} = svg.getBoundingClientRect();
  if (width <= 0 || height <= 0) return;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  label.removeAttribute("transform");
  label.setAttribute("x", width / 2); label.setAttribute("y", height / 2);
  label.style.fontSize = "32px";
  const measured = label.getBBox();
  const scale = Math.min(width * .9 / measured.width, height * .9 / measured.height);
  if (!Number.isFinite(scale) || scale <= 0) return;
  label.style.fontSize = `${32 * scale}px`;
  const fitted = label.getBBox();
  label.setAttribute("transform", `translate(${width / 2 - fitted.x - fitted.width / 2} ${height / 2 - fitted.y - fitted.height / 2})`);
}
new ResizeObserver(fitProjectionPlaceholder).observe($("projection"));
window.addEventListener("resize", fitProjectionPlaceholder);

function draw2D() {
  const svg = $("projection");
  if (!target && concentrating && !poseOverrides.size) return;
  svg.closest(".panel").classList.toggle("projection-inactive", !concentrating || !!poseOverrides.size || !target?.daylight);
  svg.replaceChildren();
  $("metrics").replaceChildren();
  $("candidates").replaceChildren();
  const sourcesCard = $("candidates").closest("details");
  sourcesCard.classList.add("sources-empty");
  $("counts").textContent = "0 实际重叠光路";
  if (!concentrating) {
    svg.setAttribute("viewBox", "0 0 100 100");
    const emptyLabel = svgElement("text", {x:50,y:50,class:"empty-projection-label"});
    emptyLabel.textContent = "镜面零位"; svg.append(emptyLabel);
    fitProjectionPlaceholder();
    $("planeNote").textContent="";
    $("areaNote").replaceChildren();

    return;
  }
  if (poseOverrides.size) { $("planeNote").textContent="图案展示模式：二维投影与效率计算暂不适用；恢复跟踪后显示。"; $("areaNote").textContent=""; return; }
  if (!target?.daylight) {
    $("planeNote").textContent = !target
      ? "正在更新投影…"
      : "夜间：阴影、遮挡及姿态不适用。";
    $("areaNote").textContent = "";
    return;
  }
  const ray = $("plane").value === "ray" && mode !== "joint",
    view = target.views[ray ? mode : "mirror"],
    p = view.polygons;
  const pts = p.target.flatMap((x) => x.outer),
    xs = pts.map((x) => x[0]),
    ys = pts.map((x) => -x[1]),
    minx = Math.min(...xs),
    miny = Math.min(...ys),
    dx = Math.max(...xs) - minx,
    dy = Math.max(...ys) - miny,
    pad = Math.max(dx, dy) * 0.14;
  svg.setAttribute(
    "viewBox",
    `${minx - pad} ${miny - pad} ${dx + 2 * pad} ${dy + 2 * pad}`,
  );
  const add = (parts, color, opacity = 1, stroke = "none") => {
    const el = svgElement("path", {
      d: svgPath(parts),
      fill: color,
      "fill-opacity": opacity,
      "fill-rule": "evenodd",
      stroke,
      "stroke-width": ".06",
      "vector-effect": "non-scaling-stroke",
    });
    svg.append(el);
    return el;
  };
  add(p.target, colors.body);
  if (mode === "joint") {
    add(p.visible, colors.body);
    add(p.shadow_only, colors.shadow);
    add(p.blocking_only, colors.blocking);
    add(p.overlap, colors.overlap);
  } else {
    add(p.target, colors.body);
    add(p[mode], colors[mode]);
  }
  add(p.target, "none", 1, colors.body);
  // Axis ticks make the coordinates inspectable without distorting aspect ratio.
  const text = (x, y, t) => {
    const el = svgElement("text", {
      x,
      y,
      fill: "#9db9c5",
      "font-size": Math.max(dx, dy) * 0.033,
    });
    el.textContent = t;
    svg.append(el);
  };
  text(
    minx,
    miny + dy + pad * 0.65,
    `u →  ${minx.toFixed(1)} … ${(minx + dx).toFixed(1)} m`,
  );
  text(
    minx,
    miny - pad * 0.35,
    `v ↑  ${(-miny - dy).toFixed(1)} … ${(-miny).toFixed(1)} m`,
  );
  // Hover a coloured region identifies its physical cause in 3D.
  svg.onpointermove = (e) => {
    const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(
      svg.getScreenCTM().inverse(),
    );
    const modes = mode === "joint" ? ["shadow", "blocking"] : [mode];
    const contributors = modes.flatMap((m) => view.contributors[m]);
    const match = contributors.find((c) =>
      insideParts([pt.x, -pt.y], c.polygons),
    );
    setHover(match?.mirror_id ?? null);
  };
  svg.onpointerleave = () => setHover(null);
  svg.onclick = () => {
    if (hovered) selectTarget(hovered);
  };
  $("planeNote").textContent = ray
    ? `沿 ${mode === "shadow" ? "s" : "rᵢ"} 投影到光线法向平面；面积含余弦投影因子。`
    : "两类损失在同一实际镜面平面上求并集；u、v 为局部正交坐标。";
  const ef = target.efficiencies;
  const f = ef;
  const card = (label, value, className = "") => {
    const el = document.createElement("div");
    el.className = "metric " + className;
    const name = document.createElement("span"); name.textContent = label;
    const number = document.createElement("strong");
    number.textContent = Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—";
    el.append(name, number); return el;
  };
  const total = card("总光学效率", ef.eta_optical, "metric-total");
  $("metrics").append(total);
  {
    const fixed = document.createElement("div"); fixed.className = "metric-fixed";
    fixed.append(card("镜面反射率", f.mirror_reflectivity), card("镜面清洁度", f.mirror_cleanliness));
    $("metrics").append(fixed);
  }
  const joint = document.createElement("section"); joint.className = "metric-joint";
  joint.setAttribute("aria-label", "联合效率及阴影遮挡分项");
  joint.append(card("联合效率", ef.eta_joint, "metric-joint-total"));
  const children = document.createElement("div"); children.className = "metric-children";
  children.append(card("阴影效率", ef.eta_shadow), card("遮挡效率", ef.eta_blocking));
  const note = document.createElement("small"); note.textContent = "两类损失按并集计算，重叠部分仅计一次";
  joint.append(children, note); $("metrics").append(joint);
  {
    const transport = document.createElement("div"); transport.className = "metric-transport";
    transport.append(card("余弦效率", f.eta_cosine), card("沿程透过率", f.eta_atmosphere), card("接收器截获率", f.eta_intercept));
    $("metrics").append(transport);
  }
  const area = Math.max(0, view.areas.target),
    lost = Math.max(0, Math.min(area, mode === "joint" ? area - view.areas.visible : view.areas[mode]));
  const areaNote = $("areaNote");
  areaNote.replaceChildren();
  const heading=document.createElement("h3"); heading.textContent="镜面 "+selected; areaNote.append(heading);
  for (const [label,value] of [["轮廓面积",area.toFixed(3)+" m²"],["损失面积",lost.toFixed(3)+" m²"],["有效面积",(area-lost).toFixed(3)+" m²"],["反射面积",Number(target.reflective_area_m2).toFixed(3)+" m²"]]) {
    const item=document.createElement("div");
    const name=document.createElement("span"); name.textContent=label;
    const number=document.createElement("strong"); number.textContent=value.replace(" m²", "");
    const unit=document.createElement("span"); unit.className="area-unit"; unit.textContent="m²";
    item.append(name,number,unit); areaNote.append(item);
  }
  const modes = mode === "joint" ? ["shadow", "blocking"] : [mode];
  let candidates = 0,
    occluders = 0;
  const labels = {
    overlap: "实际产生重叠",
    depth_rejected: "深度裁剪排除",
    degenerate: "投影退化",
    no_overlap: "与目标无面积交集",
  };
  for (const m of modes) {
    const v = target.modes[m];
    const rows = v.decisions.filter(row => row.reason === "overlap" && row.overlap_area_m2 > 0);
    candidates += rows.length;
    occluders += rows.length;
    for (const row of rows) {
      const tr = document.createElement("tr");
      const td = document.createElement("td"),
        b = document.createElement("button");
      b.textContent = row.mirror_id;
      b.onclick = () => selectTarget(row.mirror_id);
      td.append(b);
      tr.append(td);
      for (const text of [
        m === "shadow" ? "阴影" : "遮挡",
        labels[row.reason],
        (row.overlap_area_m2 ?? 0).toFixed(4),
      ]) {
        const cell = document.createElement("td");
        cell.textContent = text;
        tr.append(cell);
      }
      tr.onpointerenter = () => setHover(row.mirror_id);
      tr.onpointerleave = () => setHover(null);
      $("candidates").append(tr);
    }
  }
  $("counts").textContent = `${occluders} 实际重叠光路`;
  sourcesCard.classList.toggle("sources-empty", candidates === 0);
}
function setHover(id) {
  if (hovered === id) return;
  hovered = id;
  if (id && concentrating && efficiencyEnabled && efficiencyData) {
    const index = mirrorIndex.get(id), eta = efficiencyData.values[index],
      factors = efficiencyData.factors;
    const centre = target?.mirror_id === id ? target.centre :
      frame.vertices[index].reduce((sum, point) => sum.map((v, j) => v + point[j]), [0, 0, 0])
        .map((v) => v / frame.vertices[index].length);
    $("hover").textContent = `镜面 ${id} · 中心坐标 x=${centre[0].toFixed(3)} m, y=${centre[1].toFixed(3)} m\n` +
      `总效率 ${(eta * 100).toFixed(2)}%（余弦 ${(factors.eta_cosine[index] * 100).toFixed(1)}% · 联合 ${(factors.eta_joint[index] * 100).toFixed(1)}% · ` +
      `截获 ${(factors.eta_intercept[index] * 100).toFixed(1)}% · 目标塔 ${efficiencyData.tower_ids[index]}）`;
  } else if (id) {
    const index = mirrorIndex.get(id);
    const centre = target?.mirror_id === id ? target.centre :
      frame?.vertices?.[index]?.reduce((sum, point) => sum.map((v, j) => v + point[j]), [0, 0, 0])
        .map((v) => v / frame.vertices[index].length);
    $("hover").textContent = centre
      ? `镜面 ${id}${towerColorEnabled && frame.tower_ids ? ` · 当前目标塔 ${frame.tower_ids[index]}` : ""}\n中心坐标 x=${centre[0].toFixed(3)} m, y=${centre[1].toFixed(3)} m`
      : `镜面 ${id}`;
  } else $("hover").textContent = "";
  colorMirrors();
}
let down;
renderer.domElement.addEventListener(
  "pointerdown",
  (e) => (down = [e.clientX, e.clientY]),
);
function hit(e) {
  if (!mesh || busy) return null;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.set(
    ((e.clientX - rect.left) / rect.width) * 2 - 1,
    (-(e.clientY - rect.top) / rect.height) * 2 + 1,
  );
  raycaster.setFromCamera(pointer, camera);
  const h = raycaster.intersectObject(mesh)[0];
  return h?.instanceId === undefined ? null : meta.mirror_ids[h.instanceId];
}
renderer.domElement.addEventListener("pointerup", (e) => {
  if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) {
    const id = hit(e);
    if (id) selectTarget(id);
  }
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (e.buttons === 0) setHover(hit(e));
});
renderer.domElement.addEventListener("pointerleave", () => setHover(null));
async function nativeRequest(method, path, payload) {
  if (window.heliostatNative?.requestAsync) {
    return androidRequest(method, path, payload);
  }
  if (window.heliostatNative?.request) {
    const raw = await window.heliostatNative.request(method, path, JSON.stringify(payload));
    const data = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (data?.error) throw new Error(data.error);
    return data;
  }
  if (window.webkit?.messageHandlers?.heliostatNative) {
    const data = await window.webkit.messageHandlers.heliostatNative.postMessage({method, path, payload});
    if (data?.error) throw new Error(data.error);
    return data;
  }
  if (location.protocol === "file:")
    throw new Error("离线计算内核未连接；请重新安装完整 APK。");
  return null;
}
async function api(path, query = {}) {
  if (["frame","target","efficiencies"].includes(path)) query = {...query, tower_strategy: "independent"};
  const local = await nativeRequest("GET", path, query);
  if (local !== null) return local;
  const r = await fetch("/api/" + path + "?" + new URLSearchParams(query));
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "请求失败");
  return data;
}
async function apiPost(path, body) {
  const local = await nativeRequest("POST", path, body);
  if (local !== null) return local;
  const r = await fetch("/api/" + path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "操作失败");
  return data;
}
function status(text, error = false) {
  // Routine progress and success messages duplicate the scene and interrupt playback.
  $("statusHeading").textContent = error ? text : "";
  $("statusDetail").textContent = webglUnavailable ? "三维视图不可用：设备未启用 WebGL" : "";
  $("statusTime").textContent = "";
  $("status").classList.toggle("error", error);
  $("status").title = error ? text : "";
  $("status").hidden = !error && !webglUnavailable;

}
function formatRange(values, digits = 3) {
  const [minimum, maximum] = values;
  const format = (value) => Number(value.toFixed(digits)).toLocaleString("zh-CN");
  return Math.abs(maximum - minimum) < 1e-9
    ? format(minimum)
    : `${format(minimum)}–${format(maximum)}`;
}
function showHeliostatSummary() {
  const spec = meta.heliostat_summary;
  $("heliostatSpecMode").textContent = spec.uniform_dimensions ? "统一规格" : "混合规格";
  $("heliostatShape").textContent = spec.shape_label;
  $("heliostatWidth").textContent = `${formatRange(spec.width_range_m)} m`;
  $("heliostatHeight").textContent = `${formatRange(spec.height_range_m)} m`;
  $("heliostatArea").textContent = `${formatRange([spec.reflective_area_m2, spec.reflective_area_m2])} m²`;
  const groundHeight = $("heliostatGroundHeight");
  groundHeight.textContent = Number.isFinite(spec.ground_height_m)
    ? `${formatRange([spec.ground_height_m, spec.ground_height_m])} m`
    : "未查到公开数据";
  groundHeight.title = spec.ground_height_note || "未使用模型 z 坐标代替实际离地高度";
}
function controlsBusy(value) {
  busy = value;
  // Camera and coloring are presentation preferences: keep them interactive
  // while a playback frame is loading, so open menus are not dismissed.
  for (const id of [
    "plant",
    "date",
    "time",
    "prev",
    "next",
  ])
    $(id).disabled = value;
  for (const id of ["designMethod", "currentLayout", "rearrange", "importPlant", "exportCoordinates"])
    $(id).disabled = value;
  updateDesignMethod();
  // Target selection remains interactive; requests wait for the active frame.
}
async function loadTime() {
  if (busy) return;
  analysisController?.stop();
  analysisSnapshot = null;
  const gen = ++generation;
  controlsBusy(true);
  const retainEfficiencyColors = efficiencyEnabled && efficiencyData !== null;
  status(retainEfficiencyColors
    ? "更新效率中"
    : "计算镜面姿态与目标镜投影…");
  if (!retainEfficiencyColors) efficiencyData = null;
  // Keep the last complete projection and metrics until the next target is ready.
  try {
    currentTime = $("time").value;
    const nextFrame = await api("frame", { time: currentTime });
    if (gen !== generation) return;
    frame = nextFrame;
    updateOperationState();
    showEfficiencyScale();
    if (!frame.daylight && efficiencyEnabled) {
      efficiencyData = null;
      showEfficiencyScale();
    }
    drawFrame();
    target = await api("target", { time: currentTime, mirror: selected });
    if (gen !== generation) return;
    drawDetails();
    draw2D();
    applyViewPreference();
    if (frame.daylight && concentrating) await loadEfficiencies(gen);
    else status(
      !concentrating ? `停止聚光 · ${frame.local_time} · 全场镜面朝下平躺，聚光输出为 0` : frame.daylight
        ? `已更新 · ${frame.local_time} · 点击“效率着色”显示逐镜总光学效率`
        : `夜间 · ${frame.local_time} · 几何效率不适用`,
    );
    desktopViewerReady = true;
    if (desktopInitializationFinished) confirmDesktopInitialization();
  } catch (e) {
    stopPlay();
    status(e.message, true);
  } finally {
    controlsBusy(false);
  }
}
function showEfficiencyScale() {
  const current = fieldEfficiencyData?.timestamp === frame?.timestamp;
  if (!concentrating || !frame?.daylight) {
    $("fieldEfficiency").textContent = "0.000%";
  } else if (current) {
    $("fieldEfficiency").textContent = `${(fieldEfficiencyData.mean * 100).toFixed(3)}%`;
  }
  const visible = concentrating && efficiencyEnabled && efficiencyData;
  $("efficiencyScale").hidden = !visible;
  const meanLabel = $("fieldEfficiency");
  meanLabel.style.color = "#ffffff";
  if (visible && current) {
    const span = Math.max(1e-12, efficiencyData.maximum - efficiencyData.minimum);
    const scaled = THREE.MathUtils.clamp((fieldEfficiencyData.mean - efficiencyData.minimum) / span, 0, 1);
    const meanColor = new THREE.Color();
    if (scaled <= 0.5) meanColor.lerpColors(efficiencyLowColor, efficiencyMidColor, scaled * 2);
    else meanColor.lerpColors(efficiencyMidColor, efficiencyHighColor, (scaled - 0.5) * 2);
    meanLabel.style.color = `#${meanColor.getHexString()}`;
  }
  if (!visible) return;
  $("efficiencyMin").textContent = `${(efficiencyData.minimum * 100).toFixed(1)}%`;
  $("efficiencyMax").textContent = `${(efficiencyData.maximum * 100).toFixed(1)}%`;
}
async function loadEfficiencies(gen = generation) {
  if (!concentrating) { showEfficiencyScale(); return status("停止聚光 · 全场镜面朝下平躺，聚光输出为 0"); }
  if (!frame?.daylight) throw new Error("太阳在地平线以下，不能进行效率着色");
  status(`正在计算全部 ${meta.mirror_ids.length.toLocaleString()} 面镜的总光学效率…`);
  const data = analysisSnapshot?.timestamp === currentTime ? {...analysisSnapshot.efficiencies,mean:analysisSnapshot.mean} : await api("efficiencies", {time: currentTime});
  if (gen !== generation || data.timestamp !== frame.timestamp) return;
  efficiencyData = data;
  fieldEfficiencyData = data;
  showEfficiencyScale();
  drawDetails();
  status(
    `效率着色完成 · ${data.mirror_count.toLocaleString()} 面镜 · ` +
    `平均 ${(data.mean * 100).toFixed(2)}% · 点击或悬停镜面查看`,
  );
}
$("efficiencyColor").onclick = async () => {
  if (efficiencyEnabled) {
    efficiencyEnabled = false;
    efficiencyData = null;
    $("efficiencyColor").classList.remove("active");
    $("efficiencyColor").setAttribute("aria-pressed", "false");
    showEfficiencyScale();
    drawDetails();
    status(`已关闭效率着色 · ${frame.local_time}`);
    return;
  }
  efficiencyEnabled = true;
  towerColorEnabled = false;
  $("towerColor").classList.remove("active");
  $("towerColor").setAttribute("aria-pressed", "false");
  $("efficiencyColor").classList.add("active");
  $("efficiencyColor").setAttribute("aria-pressed", "true");
  // Playback already fetches efficiencies for the next daylight frame. Apply
  // the preference immediately without starting a competing frame request.
  showEfficiencyScale();
  drawDetails();
  if (busy || !frame?.daylight || !concentrating) return;
  controlsBusy(true);
  try { await loadEfficiencies(); }
  catch (e) {
    // A failed request must not overwrite a preference changed during playback.
    status(e.message, true);
  } finally { controlsBusy(false); }
};
$("towerColor").onclick = () => {
  if (meta.tower_count < 2) return;
  towerColorEnabled = !towerColorEnabled;
  efficiencyEnabled = false;
  efficiencyData = null;
  $("efficiencyColor").classList.remove("active");
  $("efficiencyColor").setAttribute("aria-pressed", "false");
  $("towerColor").classList.toggle("active", towerColorEnabled);
  $("towerColor").setAttribute("aria-pressed", String(towerColorEnabled));
  showEfficiencyScale();
  drawDetails();
  status(towerColorEnabled
    ? `已按当前目标塔着色 · ${meta.towers.map((tower, i) => `${tower.name} ${towerColors[i % towerColors.length]}`).join(" · ")}`
    : `已关闭目标塔着色 · ${frame.local_time}`);
};
let targetSelectionRequest = 0;
async function selectTarget(id) {
  const request = ++targetSelectionRequest;
  await waitForIdle(()=>busy,new AbortController().signal);
  if(request !== targetSelectionRequest) return;
  if (!meta.mirror_ids.includes(id)) {
    status("未找到该镜面，请输入当前镜场中的有效编号", true);
    return;
  }
  controlsBusy(true);
  try {
    const result = analysisSnapshot?.timestamp === currentTime
      ? (await apiPost("analysis/instant", {time:currentTime,mirror:id,dni:analysisSnapshot.weather.dni,
          temperature_c:analysisSnapshot.weather.temperature_c,pressure_hpa:analysisSnapshot.weather.pressure_hpa,
          weather_source:analysisSnapshot.weather.source,weather_time:analysisSnapshot.weather.time,target_only:true})).target
      : await api("target", { time: currentTime, mirror: id });
    selected = id;
    target = result;
    $("mirror").value = id;
    drawDetails();
    draw2D();
    const centreText = result.centre
      ? ` · 中心坐标 x=${result.centre[0].toFixed(3)} m, y=${result.centre[1].toFixed(3)} m`
      : "";
    status(`已选择 ${id}${centreText}${result.tower_id ? ` · 当前目标塔 ${result.tower_id}` : ""} · 三维与二维已同步`);
  } catch (e) {
    status(e.message, true);
  } finally {
    controlsBusy(false);
  }
}
function setDate(date, time) {
  $("date").value = date;
  $("time").replaceChildren();
  for (const item of timesByDate.get(date) || []) {
    const op = document.createElement("option");
    op.value = item.utc;
    op.textContent = item.label;
    $("time").append(op);
  }
  if (time) $("time").value = time;
}
async function step(delta) {
  if(busy) return false;
  try {
    const value=$("analysisTime").value;
    const currentLocal=localInput(currentTime,meta.timezone);
    const matchesCurrent=value===currentLocal||(value===currentLocal.slice(0,16)&&currentLocal.endsWith(":00"));
    const instant=matchesCurrent?currentTime:zonedInstant(value,meta.timezone);
    const utc=historicalSample(new Date(Date.parse(instant)+delta*3600000).toISOString(),meta.timestamps);
    analysisController?.stop();
    const item=localItem(utc);setDate(item.date,utc);
    $("analysisTime").value=localInput(utc,meta.timezone);
    await loadTime();
    if(frame?.timestamp!==utc||!target) return false;
    $("analysisDni").value=frame.dni;
    if(Number.isFinite(frame.temperature)) $("analysisTemperature").value=frame.temperature;
    return true;
  } catch(error) { $("analysisStatus").textContent=error.message;status(error.message,true);return false; }
}
function stopPlay() {
  playing = false;
  clearTimeout(playTimer);
  $("play").textContent = "逐时播放";
  controlsBusy(busy);
}
async function playLoop() {
  if (!playing) return;
  if (!busy && !(await step(1))) { stopPlay(); return; }
  if (playing) playTimer = setTimeout(playLoop, 1000);
}
function localItem(utc) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: meta.timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZoneName: "shortOffset",
    })
      .formatToParts(new Date(utc))
      .map((p) => [p.type, p.value]),
  );
  return {
    utc,
    date: `${parts.year}-${parts.month}-${parts.day}`,
    label: `${parts.hour}:${parts.minute} ${parts.timeZoneName}`,
  };
}
$("date").onchange = () => {
  analysisController?.stop();
  stopPlay();
  if (!timesByDate.has($("date").value)) {
    status("日期不在历史数据范围内", true);
    return;
  }
  setDate($("date").value);
  loadTime();
};
$("time").onchange = () => {
  stopPlay();
  loadTime();
};
$("prev").onclick = () => {
  stopPlay();
  step(-1);
};
$("next").onclick = () => {
  stopPlay();
  step(1);
};
$("play").onclick = async () => {
  if(playing) {stopPlay();return;}
  if(busy||analysisController?.busy) return;
  analysisController?.stop();
  analysisController?.focus();
  selectedAnalysisMode="playback";playing=true;$("play").textContent="暂停";
  if(!(await step(0))) {stopPlay();return;}
  if(playing) playTimer=setTimeout(playLoop,1000);
};
$("selectMirror").onsubmit = (e) => {
  e.preventDefault();
  selectTarget($("mirror").value.trim().toUpperCase());
};
$("focus").onclick = () => { viewPreference = "focus"; animateCamera(applyViewPreference); $("viewMode").value = "focus"; };
$("overview").onclick = () => { viewPreference = "overview"; animateCamera(overview); $("viewMode").value = "overview"; };
$("viewMode").onchange = () => {
  viewPreference = $("viewMode").value;
  if (viewPreference === "focus") animateCamera(applyViewPreference);
  else animateCamera(overview);
};
$("viewIso").onclick = () => {
  if (viewPreference === "focus") animateCamera(focusIsometric);
  else animateCamera(overview);
};
$("viewTop").onclick = () => {
  if (viewPreference === "focus") animateCamera(focusTopView);
  else animateCamera(topView);
};
$("neighbors").checked = false;
$("neighbors").onchange = () => {
  $("neighbors").classList.toggle("active", $("neighbors").checked);
  $("neighbors").setAttribute("aria-pressed", String($("neighbors").checked));
  colorMirrors();
};
$("neighbors").onclick = () => {
  $("neighbors").checked = !$("neighbors").checked;
  $("neighbors").dispatchEvent(new Event("change"));
};
for (const button of document.querySelectorAll("[data-mode]"))
  button.onclick = () => {
    mode = button.dataset.mode;
    document
      .querySelectorAll("[data-mode]")
      .forEach((b) => b.classList.toggle("active", b === button));
    if (mode === "joint") $("plane").value = "mirror";
    $("plane").disabled = mode === "joint";
    drawDetails();
    draw2D();
  };
$("plane").onchange = draw2D;
function updateDesignMethod() {
  const method = $("designMethod").value;
  for (const label of $("designParameters").querySelectorAll("[data-methods]")) {
    const visible = label.dataset.methods.split(" ").includes(method);
    label.hidden = !visible;
    for (const input of label.querySelectorAll("input")) input.disabled = busy || !visible;
  }
  $("designParameters").hidden = method === "imported";
  $("rearrange").hidden = method === "imported";
  $("importPlant").hidden = method !== "imported";
}
function showLayoutSelectors() {
  const plants = meta.plants.filter(item => item.builtin);
  const sourceId = meta.environment_plant_id || meta.active_plant;
  const sourcePlant = plants.find(item => item.id === sourceId);
  const populate = (select, items) => {
    select.replaceChildren();
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.id; option.textContent = item.name; select.append(option);
    }
  };
  populate($("plant"), plants);
  if (!sourcePlant) {
    const option = document.createElement("option");
    option.value = ""; option.textContent = "自定义场址"; option.disabled = true;
    $("plant").prepend(option);
  }
  $("plant").value = sourcePlant?.id || "";
  const source = meta.plants.find(item => item.id === sourceId);
  const items = [{id:sourceId, name:"原厂参考布局"}];
  const used = new Map();
  for (const design of meta.plants.filter(item => !item.builtin && item.id !== sourceId && item.environment_plant_id === sourceId)) {
    const prefix = source?.name + " · ";
    const name = design.name.startsWith(prefix) ? design.name.slice(prefix.length) : design.name;
    const count = (used.get(name) || 0) + 1; used.set(name, count);
    items.push({id:design.id, name: `${name} · 方案 ${count}`});
  }
  populate($("currentLayout"), items);
  $("currentLayout").value = meta.active_plant;
  $("currentLayout").onchange = () => selectLayout($("currentLayout").value);
  const activeDesign = meta.plants.find(item => item.id === meta.active_plant && !item.builtin);
  if (activeDesign?.design_method) $("designMethod").value = activeDesign.design_method;
  if (meta.active_plant !== sourceId) $("mobileManage").open = true;
  $("designMethod").onchange = updateDesignMethod;
  updateDesignMethod();
}
async function init() {
  if (window.webkit?.messageHandlers?.heliostatUpdateHealthy || window.chrome?.webview) {
    const preferences = await api("update/preferences");
    for (const [key, value] of Object.entries(preferences)) {
      if (key.startsWith("heliostat-operation-") && localStorage.getItem(key) === null) localStorage.setItem(key, value);
    }
  }
  meta = await api("meta");
  readOperationParameters();
  mirrorIndex = new Map(meta.mirror_ids.map((id, i) => [id, i]));
  selected = meta.default_mirror;
  $("mirror").value = selected;
  showLayoutSelectors();
  $("timezoneLabel").textContent=`时刻 · ${meta.timezone}`;
  $("plantBadge").textContent = `${meta.plant_name} · ${meta.tower_count} 座塔 · 全模型 ${meta.mirror_ids.length.toLocaleString()} 面镜`;
  $("towerColor").hidden = meta.tower_count < 2;
  showHeliostatSummary();
  $("layoutStatus").textContent=meta.layout_status;
  const loc=meta.site_location;
  const coordinateText = loc
    ? `${loc.latitude.toFixed(6)}°, ${loc.longitude.toFixed(6)}° · ${loc.accuracy_label}`
    : "坐标未提供";
  $("source").textContent = `${coordinateText} · ${meta.source}`;
  if (loc?.source_url) {
    $("source").append(document.createTextNode(" · "));
    const locationLink=document.createElement("a");
    locationLink.href=loc.source_url; locationLink.textContent="坐标来源"; locationLink.target="_blank";
    $("source").append(locationLink);
  }
  $("sourceNote").textContent = [loc?.note, meta.note].filter(Boolean).join(" ");
  if (meta.plant_details) {
    const d=meta.plant_details;
    $("source").replaceChildren(document.createTextNode(
      `${d.location} · ${coordinateText} · ${d.capacity_mw} ${d.capacity_kind === "thermal" ? "MWth" : "MW"} · ` +
      (d.reported_towers ? `${d.reported_towers} 座塔、间距约 ${d.reported_tower_spacing_m} m · ` : "") +
      (d.tower_height_m ? `塔高／瞄准标高 ${d.tower_height_m} m · ` : `模型瞄准标高 ${d.model_tower_height_m} m（估值） · `) +
      `${d.receiver_geometry_status === "published" ? "接收器" : "模型接收器（近似）"} R ${d.model_receiver_radius_m} m × H ${d.model_receiver_height_m} m · ` +
      `定日镜 ${d.model_heliostat_width_m} m × ${d.model_heliostat_height_m} m · ` +
      (d.reported_heliostats ? `${d.reported_heliostats.toLocaleString()} 面公开镜数` : "公开镜数缺失") + " · "
    ));
    const link=document.createElement("a"); link.href=d.source_url; link.textContent="项目资料"; link.target="_blank";
    $("source").append(link);
    if (loc?.source_url && loc.source_url !== d.source_url) {
      $("source").append(document.createTextNode(" · "));
      const locationLink=document.createElement("a");
      locationLink.href=loc.source_url; locationLink.textContent="高精度坐标来源"; locationLink.target="_blank";
      $("source").append(locationLink);
    }
    $("sourceNote").textContent=[meta.layout_status + "。", loc?.note, d.note, d.model_receiver_note, meta.note]
      .filter(Boolean).join(" ");
  }
  const temperatureLabels = {historical:"历史气象温度", era5_land_reanalysis:"ERA5-Land 历史再分析气温，非现场实测", illustrative_simulation:"模拟气温（季节与昼夜变化，非实测）"};
  if (meta.temperature_source) {
    $("sourceNote").append(document.createTextNode(" 气温：" + temperatureLabels[meta.temperature_source] + "。"));
    if (meta.temperature_source === "era5_land_reanalysis") {
      const link = document.createElement("a"); link.href="https://open-meteo.com/en/docs/historical-weather-api"; link.textContent="气温数据来源"; link.target="_blank"; $("sourceNote").append(link);
    }
  }
  for (const t of meta.timestamps) {
    const item = localItem(t);
    if (!timesByDate.has(item.date)) timesByDate.set(item.date, []);
    timesByDate.get(item.date).push(item);
  }
  const dates = [...timesByDate.keys()];
  $("date").min = dates[0];
  $("date").max = dates.at(-1);
  const suggestionStep = Math.max(1, Math.ceil(meta.mirror_ids.length / 5000));
  for (let i = 0; i < meta.mirror_ids.length; i += suggestionStep) {
    const op = document.createElement("option");
    op.value = meta.mirror_ids[i];
    $("mirrorIds").append(op);
  }
  for (const towerData of meta.towers) {
    const rc = towerData.receiver,
      base = towerData.base,
      towerHeight = rc.centre[2] - base[2],
      tower = new THREE.Mesh(
      new THREE.CylinderGeometry(2, 3, towerHeight, 20),
      new THREE.MeshBasicMaterial({ color: "#57717e" }),
    );
    tower.rotation.x = Math.PI / 2;
    tower.position.set(base[0], base[1], base[2] + towerHeight / 2);
    scene.add(tower);
    const receiver = new THREE.Mesh(
      new THREE.CylinderGeometry(rc.radius, rc.radius, rc.height, 32),
      new THREE.MeshBasicMaterial({ color: "#d3b66b" }),
    );
    receiver.rotation.x = Math.PI / 2;
    receiver.position.copy(V(rc.centre));
    scene.add(receiver);
  }
  const item = localItem(meta.default_time);
  setDate(item.date, item.utc);
  $("plane").disabled = true;
  await loadTime();
  analysisController?.initialize();
  if (location.hash.startsWith("#layout-field-top")) {
    const card = document.querySelector("main > .spatial");
    card.open = true;
    viewPreference = "overview";
    $("viewMode").value = "overview";
    await new Promise(resolve => requestAnimationFrame(resolve));
    sizeSceneArea();
    await new Promise(resolve => requestAnimationFrame(resolve));
    resize();
    cameraTransition = null;
    topView();
    const destination = document.querySelector(".environment-summary");
    destination.tabIndex = -1;
    destination.classList.add("navigation-focus");
    destination.addEventListener("blur", () => destination.classList.remove("navigation-focus"), {once:true});
    destination.focus({preventScroll:true});
    destination.scrollIntoView({block:"center", behavior:"instant"});
    // Keep navigation-carried analysis state until restoreAnalysisMode consumes it.
    if(!new URLSearchParams(location.hash.slice(1)).has("analysis"))
      history.replaceState(null, "", location.pathname + location.search);
  }
}
async function confirmDesktopInitialization() {
  if (desktopInitializationConfirmed || desktopInitializationConfirming || !desktopInitializationFinished || !desktopViewerReady) return;
  if (!window.webkit?.messageHandlers?.heliostatUpdateHealthy && !window.chrome?.webview) return;
  try {
    desktopInitializationConfirming = true;
    const health = await api("health");
    if (health.status !== "ok") return;
    await new Promise(resolve => requestAnimationFrame(resolve));
    if (!frame || !target || !meta || renderer.getContext().isContextLost()) return;
    desktopInitializationConfirmed = true;
    window.webkit?.messageHandlers?.heliostatUpdateHealthy?.postMessage("ready");
    window.chrome?.webview?.postMessage("update-healthy");
  } catch (error) { console.error("Update initialization confirmation failed", error); }
  finally { desktopInitializationConfirming = false; }
}
init().then(() => {
  desktopInitializationFinished = true;
  confirmDesktopInitialization();
  restoreAnalysisMode();
}).catch((e) => status(e.message, true));

function reloadLayoutInTopView() {
  // Carry the destination through reload in desktop and native WebViews.
  history.replaceState(null, "", location.pathname + location.search + "#layout-field-top");
  location.reload();
}

const analysisTransferKey = "heliostat-analysis-transfer";
function captureAnalysisMode() {
  return {mode:selectedAnalysisMode,
    time:$("analysisTime").value,dni:$("analysisDni").value,
    temperature:$("analysisTemperature").value,pressure:$("analysisPressure").value};
}
async function restoreAnalysisMode(saved) {
  if(!saved) {
    const transfer=new URLSearchParams(location.hash.slice(1)).get("analysis");
    const raw=transfer||sessionStorage.getItem(analysisTransferKey);
    if(transfer) history.replaceState(null,"",location.pathname+location.search);
    sessionStorage.removeItem(analysisTransferKey);
    if(!raw) return;
    try {saved=JSON.parse(raw);} catch {return;}
  }
  selectedAnalysisMode=saved.mode;
  if(saved.mode==="historical") return;
  $("analysisTime").value=saved.time;
  $("analysisDni").value=saved.dni;
  $("analysisTemperature").value=saved.temperature;
  $("analysisPressure").value=saved.pressure;
  if(saved.mode==="playback") {
    selectedAnalysisMode="playback";playing=true;$("play").textContent="暂停";
    try { historicalSample(zonedInstant(saved.time,meta.timezone),meta.timestamps); }
    catch { $("analysisTime").value=localInput(meta.default_time,meta.timezone); }
    if(!(await step(0))) {stopPlay();$("analysisPanel").open=true;return;}
    if(playing) playTimer=setTimeout(playLoop,1000);
  } else analysisController.resume(saved.mode==="live");
}
let switchingLayout=false;
async function selectLayout(id) {
  if(!id||switchingLayout) return;
  switchingLayout=true;
  const saved=captureAnalysisMode();
  stopPlay();analysisController?.stop();
  await waitForIdle(()=>busy,new AbortController().signal);
  controlsBusy(true); status("正在载入镜场…");
  try {
    await apiPost("plants/select", {plant_id:id});
    sessionStorage.setItem(analysisTransferKey,JSON.stringify(saved));
    history.replaceState(null,"",location.pathname+location.search+"#layout-field-top&analysis="+encodeURIComponent(JSON.stringify(saved)));
    location.reload();
  } catch(e) {
    status(e.message,true);controlsBusy(false);switchingLayout=false;
    await restoreAnalysisMode(saved);
  }
}
$("plant").onchange = () => selectLayout($("plant").value);
$("importPlant").onclick=()=>$("importDialog").showModal();

for (const b of document.querySelectorAll("[data-close]")) b.onclick=()=>b.closest("dialog").close();
$("importForm").onsubmit=async(e)=>{
  e.preventDefault(); const f=new FormData(e.currentTarget), file=f.get("file");
  if (!(file instanceof File) || !file.size) return;
  const body=Object.fromEntries([...f.entries()].filter(([k])=>k!=="file")); body.csv=await file.text(); body.plant_id=meta.environment_plant_id || meta.active_plant;
  $("importDialog").close(); stopPlay(); controlsBusy(true);
  $("designProgress").hidden=false; $("designProgress").textContent="正在校验并导入布局…";
  try { analysisController?.stop(); await apiPost("plants/import",body); reloadLayoutInTopView(); }
  catch(err){ $("designProgress").hidden=true; status(err.message,true); controlsBusy(false); }
};
$("rearrangeForm").onsubmit=async(e)=>{
  e.preventDefault(); const body=Object.fromEntries(new FormData(e.currentTarget));
  body.plant_id=meta.environment_plant_id || meta.active_plant;
  if (body.scheme === "imported") return $("importDialog").showModal();
  stopPlay(); controlsBusy(true);
  $("designProgress").hidden=false; $("designProgress").textContent="正在基于当前电厂生成布局…";
  try { analysisController?.stop(); await apiPost("layout/rearrange",body); reloadLayoutInTopView(); }
  catch(err){ $("designProgress").hidden=true; status(err.message,true); controlsBusy(false); }
};

// Presentation poses are deliberately separate from tracking/optical calculations.

function displayVertices(i) {
  if (!concentrating) {
    const vs = frame.vertices?.[i];
    const c = frame.centres?.[i] || vs.reduce((a,b)=>a.map((x,j)=>x+b[j]/4),[0,0,0]);
    const dimensions = meta.mirror_dimensions?.[i] || (vs ? [V(vs[0]).distanceTo(V(vs[1])), V(vs[0]).distanceTo(V(vs[3]))] : [meta.heliostat_summary.width_range_m[0], meta.heliostat_summary.height_range_m[0]]);
    return parkedVertices(c, ...dimensions);
  }
  if (!poseOverrides.has(meta.mirror_ids[i])) return frame.vertices[i];
  const vs = frame.vertices[i].map(V);
  const c = vs.reduce((a,b)=>a.add(b),new THREE.Vector3()).multiplyScalar(0.25);
  const w = vs[0].distanceTo(vs[1]), h = vs[0].distanceTo(vs[3]);
  const normal = V(poseOverrides.get(meta.mirror_ids[i])).normalize();
  const u = new THREE.Vector3(0,1,0).cross(normal);
  if (u.lengthSq()<1e-8) u.set(1,0,0); else u.normalize();
  const v = normal.clone().cross(u).normalize();
  return [[-1,-1],[1,-1],[1,1],[-1,1]].map(([x,y])=>c.clone().addScaledVector(u,x*w/2).addScaledVector(v,y*h/2).toArray());
}
function poseIndices() {
  const ids = $("poseIds").value.trim().split(/[\s,，]+/).filter(Boolean);
  const y = Number($("poseY").value), tolerance = Number($("poseTolerance").value);
  return meta.mirror_ids.map((id,i)=>i).filter(i=>ids.length ? ids.includes(meta.mirror_ids[i]) :
    Math.abs((frame.centres?.[i] || frame.vertices[i].reduce((a,b)=>a.map((x,j)=>x+b[j]/4),[0,0,0]))[1]-y)<=tolerance);
}
$("poseApply").onclick = () => {
  if (!frame?.daylight) return status("请选择白天时刻",true);
  const tilt=THREE.MathUtils.degToRad(Number($("poseTilt").value));
  const az=THREE.MathUtils.degToRad(Number($("poseAzimuth").value));
  const normal=[Math.sin(tilt)*Math.sin(az),Math.sin(tilt)*Math.cos(az),Math.cos(tilt)];
  const indices=poseIndices();
  if (!$("poseAppend").checked) poseOverrides.clear();
  indices.forEach(i=>poseOverrides.set(meta.mirror_ids[i],normal));
  drawDetails();draw2D();status(`图案展示模式 · 已调整 ${indices.length} 面镜子 · 效率仍对应跟踪姿态`);
};
function showSceneTopView() {
  const card = document.querySelector("main > .spatial");
  card.open = true;
  viewPreference = "overview";
  $("viewMode").value = "overview";
  $("neighbors").checked = false;
  $("neighbors").dispatchEvent(new Event("change"));
  requestAnimationFrame(() => {
    sizeSceneArea();
    requestAnimationFrame(() => {
      resize();
      cameraTransition = null;
      topView();
      const heading = card.querySelector("summary");
      heading.classList.add("navigation-focus");
      heading.addEventListener("blur", () => heading.classList.remove("navigation-focus"), {once:true});
      heading.addEventListener("keydown", () => heading.classList.remove("navigation-focus"), {once:true});
      heading.focus({preventScroll:true});
      card.scrollIntoView({block:"start", behavior:"smooth"});
    });
  });
}
$("poseReset").onclick = () => { poseOverrides.clear();drawDetails();draw2D();status("已恢复太阳跟踪姿态");showSceneTopView(); };
$("poseImage").onchange = async event => {
  if (!frame?.daylight || !event.target.files[0]) return;
  const bitmap=await createImageBitmap(event.target.files[0]);
  const canvas=document.createElement("canvas");canvas.width=256;canvas.height=256;
  const ctx=canvas.getContext("2d");ctx.fillStyle="white";ctx.fillRect(0,0,256,256);ctx.drawImage(bitmap,0,0,256,256);
  applyPatternCanvas(canvas);bitmap.close();
};
function applyPatternCanvas(canvas) {
  const pixels=canvas.getContext("2d").getImageData(0,0,canvas.width,canvas.height).data;
  const centres=frame.vertices.map(v=>v.reduce((a,b)=>a.map((x,j)=>x+b[j]/4),[0,0,0]));
  const xs=centres.map(c=>c[0]),ys=centres.map(c=>c[1]);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  poseOverrides.clear();
  centres.forEach((c,i)=>{const x=Math.round((c[0]-minX)/(maxX-minX||1)*(canvas.width-1)),y=Math.round((maxY-c[1])/(maxY-minY||1)*(canvas.height-1)),k=(y*canvas.width+x)*4;
    if ((pixels[k]+pixels[k+1]+pixels[k+2])/3<128) poseOverrides.set(meta.mirror_ids[i],[0,0,1]);});
  drawDetails();draw2D();status(`图案展示模式 · ${poseOverrides.size} 面镜子水平 · 黑色图案对应水平镜面`);
}
$("poseTextApply").onclick = () => {
  const text=$("poseText").value;
  // Preserve indentation for measurement and rendering; only use trim to detect an empty pattern.
  if (!text.trim()) {
    poseOverrides.clear();
    drawDetails(); draw2D();
    showSceneTopView();
    return status("已清空文字图案，恢复当前启停状态对应的姿态");
  }
  if (!frame?.daylight) return status("请选择白天时刻",true);
  const canvas=document.createElement("canvas"), rect=$("scene").getBoundingClientRect();
  const centres=frame.vertices.map(v=>v.reduce((a,b)=>a.map((x,j)=>x+b[j]/4),[0,0,0]));
  const xs=centres.map(c=>c[0]),ys=centres.map(c=>c[1]);
  const aspect=(Math.max(...xs)-Math.min(...xs))/(Math.max(...ys)-Math.min(...ys)||1);
  const fitWidth=Math.max(128,Math.min(1024,rect.width,rect.height*aspect));
  canvas.width=Math.round(fitWidth);canvas.height=Math.max(64,Math.round(fitWidth/aspect));
  const ctx=canvas.getContext("2d");ctx.fillStyle="white";ctx.fillRect(0,0,canvas.width,canvas.height);
  const lines=text.split(/\r?\n/);
  ctx.font="bold 100px sans-serif";
  const measuredWidth=Math.max(1,...lines.map(line=>ctx.measureText(line).width));
  const automatic=Math.min(canvas.width*0.75/measuredWidth*100,canvas.height*0.70/(lines.length*1.2));
  const size=Math.max(1,automatic*patternFontScale);
  $("poseFontSize").value=String(size);$("poseFontLabel").textContent=patternFontScale===1?"自动":`${Math.round(patternFontScale*100)}%`;
  ctx.font=`bold ${size}px sans-serif`;ctx.fillStyle="black";ctx.textAlign="center";ctx.textBaseline="middle";
  lines.forEach((line,i)=>ctx.fillText(line,canvas.width/2,canvas.height/2+(i-(lines.length-1)/2)*size*1.2));
  applyPatternCanvas(canvas);
  showSceneTopView();
};
$("exportCoordinates").onclick = async () => {
  if (!meta || !frame) return;
  const rows=["mirror_id,x_east_m,y_north_m,z_up_m,normal_east,normal_north,normal_up"];
  meta.mirror_ids.forEach((id,i)=>{
    const vs=displayVertices(i);
    const c=vs?vs.reduce((a,b)=>a.map((x,j)=>x+b[j]/4),[0,0,0]):frame.centres[i];
    const n=vs?V(vs[1]).sub(V(vs[0])).cross(V(vs[3]).sub(V(vs[0]))).normalize().toArray():["","",""];
    rows.push([id,...c,...n].join(","));
  });
  const text="\ufeff"+rows.join("\r\n"),name="heliostat-coordinates.csv";
  if (window.webkit?.messageHandlers?.heliostatExport) { window.webkit.messageHandlers.heliostatExport.postMessage({text});return; }
  if (window.heliostatNative?.saveFile) { window.heliostatNative.saveFile(name,text);return; }
  if (window.webkit?.messageHandlers?.heliostatNative) {
    await window.webkit.messageHandlers.heliostatNative.postMessage({method:"POST",path:"file/export",payload:{name,text}});return;
  }
  const url=URL.createObjectURL(new Blob([text],{type:"text/csv;charset=utf-8"}));
  const link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
};

function adjustPatternFont(delta) {
  patternFontScale=Math.max(0.25,Math.min(1.25,Math.round((patternFontScale+delta)*100)/100));
  $("poseFontLabel").textContent=`${Math.round(patternFontScale*100)}%`;
  if ($("poseText").value.trim()) $("poseTextApply").click();
}
$("poseText").oninput=()=>{patternFontScale=1;$("poseFontLabel").textContent="自动";};
$("poseFontDecrease").onclick=()=>adjustPatternFont(-0.1);
$("poseFontIncrease").onclick=()=>adjustPatternFont(0.1);

// Native desktop menus use the same actions as the on-page controls.
window.heliostatDesktopCommand = command => {
  if (command === "updates") return openUpdates();
  const reveal = id => {
    const editor = document.querySelector(".poseeditor");
    editor.open = true;
    $(id).scrollIntoView({behavior: "smooth", block: "center"});
    $(id).focus({preventScroll: true});
  };
  if (command === "operation") { $("operationSettings").open=true; $("operationSettings").scrollIntoView({behavior:"smooth",block:"center"}); return; }
  if (command === "text") return reveal("poseText");
  if (command === "image") { reveal("poseImage"); return $("poseImage").click(); }
  if (command === "orientation") return reveal("poseIds");
  if (command === "help") return showDesktopHelp("选择电厂和当地时刻后，可点击镜面选择目标。\n拖动旋转、滚轮缩放；视图菜单提供全场、目标、等轴测和俯视。\n文件菜单导入或导出坐标；图案菜单添加文字、图片或调整镜面朝向。\n图案姿态仅用于展示，效率仍对应太阳跟踪姿态。");
  const actions = {import: "importPlant", export: "exportCoordinates", rearrange: "rearrange", textApply: "poseTextApply", reset: "poseReset", overview: "overview", focus: "focus", iso: "viewIso", top: "viewTop", efficiency: "efficiencyColor", tower: "towerColor", play: "play", prev: "prev", next: "next"};
  if (command === "neighbors") {
    $("neighbors").checked = !$("neighbors").checked;
    return $("neighbors").dispatchEvent(new Event("change"));
  }
  if (actions[command]) $(actions[command]).click();
};

function showDesktopHelp(text) {
  const dialog = document.createElement("dialog");
  const heading = document.createElement("h2"); heading.textContent = "操作说明";
  const body = document.createElement("p"); body.textContent = text; body.style.whiteSpace = "pre-line";
  const close = document.createElement("button"); close.textContent = "关闭"; close.onclick = () => dialog.close();
  dialog.append(heading, body, close); document.body.append(dialog);
  dialog.onclose = () => dialog.remove(); dialog.showModal();
}

function operationStorageKey() { return "heliostat-operation-" + meta.active_plant; }
function readOperationParameters() {
  try { operationParameters = validateOperationParameters(JSON.parse(localStorage.getItem(operationStorageKey())) || {...defaultOperationParameters}); }
  catch { operationParameters = {...defaultOperationParameters}; }
  concentrating = false;
  for (const [key,value] of Object.entries(operationParameters)) $(key).value = value;
}
function updateOperationState() {
  concentrating = nextOperationState(concentrating, frame.dni, frame.elevation, operationParameters);
  $("operationState").textContent = concentrating ? " · 聚光运行" : " · 停止聚光（零位）";
  showEfficiencyScale();
  hovered = null; $("hover").textContent = "";
}
$("operationForm").onsubmit = async event => {
  event.preventDefault();
  if (busy) return status("请等待当前计算完成后应用启停参数", true);
  analysisController?.stop();
  controlsBusy(true);
  try {
    operationParameters = validateOperationParameters(Object.fromEntries(Object.keys(defaultOperationParameters).map(key=>[key, Number($(key).value)])));
    try { localStorage.setItem(operationStorageKey(), JSON.stringify(operationParameters)); } catch { status("设备未允许保存参数；本次运行仍会应用", true); }
    if (frame) { updateOperationState(); drawFrame(); drawDetails(); draw2D();
      if (concentrating && frame.daylight) await loadEfficiencies(); }
    status(concentrating ? "启停参数已应用 · 聚光运行" : "启停参数已应用 · 停止聚光，镜面全部回归朝下零位");
  } catch (error) { status(error.message, true); }
  finally { controlsBusy(false); }
};

const openUpdates = initUpdates(api, apiPost);


analysisController = initAnalysis({
  getContext:()=>({meta,selected,busy,currentTime,frame}),
  post:apiPost,setBusy:controlsBusy,
  onStatusChange:()=>controlsBusy(busy),
  onWeatherMissing:fields=>{
    const ids={dni:"weatherDni",temperature_c:"weatherTemperature"};
    for(const field of fields) {const id=ids[field];if(id){$(id).textContent="数据缺失";$(id).dataset.missing="true";}}
  },
  onModeChange:mode=>{selectedAnalysisMode=mode;if(mode!=="playback")stopPlay();},
  onStart:()=>{
    stopPlay();
    const card=document.querySelector("main > .spatial");
    card.open=true;
    const destination=document.querySelector(".environment-summary");
    destination.tabIndex=-1;
    destination.classList.add("navigation-focus");
    destination.addEventListener("blur",()=>destination.classList.remove("navigation-focus"),{once:true});
    destination.focus({preventScroll:true});
    destination.scrollIntoView({block:"center",behavior:"smooth"});
  },
  applyResult:result=>{
    analysisSnapshot=result;
    currentTime=result.timestamp;frame=result.frame;target=result.target;
    generation++;
    concentrating=frame.daylight;
    fieldEfficiencyData={...result.efficiencies,mean:result.mean};
    efficiencyData=efficiencyEnabled&&frame.daylight?result.efficiencies:null;
    $("operationState").textContent=frame.daylight?" · 分析工况：太阳跟踪":" · 分析工况：夜间";
    // Historical sample controls retain their valid selection. Analysis time
    // is read-only at the top and may lie outside the archived sample dates.
    if(meta.timestamps.includes(currentTime)) {
      const item=localItem(currentTime);setDate(item.date,currentTime);
    }
    drawFrame();drawDetails();draw2D();showEfficiencyScale();
  }
});
