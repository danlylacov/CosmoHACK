import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { pointAtTime, subsolarPoint, sunlitAmount, debrisTrackId } from "../lib/orbits.js";
import { addHoursIso } from "../lib/format.js";

const EARTH_RADIUS = 1;
const KM = EARTH_RADIUS / 6371;

const TEXTURES = {
  day: "/textures/earth-day.jpg",
  night: "/textures/earth-night.jpg",
  normal: "/textures/earth-normal.jpg",
  dayCdn: "https://unpkg.com/three-globe@2.44.1/example/img/earth-blue-marble.jpg",
  nightCdn: "https://unpkg.com/three-globe@2.44.1/example/img/earth-night.jpg",
  normalCdn: "https://unpkg.com/three-globe@2.44.1/example/img/earth-topology.png",
};

function latLonToVec3(lat, lon, altKm = 0) {
  const phi = THREE.MathUtils.degToRad(90 - lat);
  const theta = THREE.MathUtils.degToRad(lon + 180);
  const r = EARTH_RADIUS + altKm * KM * 8;
  return new THREE.Vector3(
    -r * Math.sin(phi) * Math.cos(theta),
    r * Math.cos(phi),
    r * Math.sin(phi) * Math.sin(theta),
  );
}

function closestPoint(pts, tMs) {
  return pointAtTime(pts, tMs) || pts[0];
}

function sunDirectionFromIso(iso) {
  const { lat, lon } = subsolarPoint(iso);
  return latLonToVec3(lat, lon, 0).normalize();
}

function loadTexture(url, colorSpace) {
  return new Promise((resolve, reject) => {
    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin("anonymous");
    loader.load(
      url,
      (tex) => {
        if (colorSpace) tex.colorSpace = colorSpace;
        tex.anisotropy = 8;
        resolve(tex);
      },
      undefined,
      reject,
    );
  });
}

async function loadMap(local, cdn, colorSpace) {
  try {
    return await loadTexture(local, colorSpace);
  } catch {
    try {
      return await loadTexture(cdn, colorSpace);
    } catch {
      return null;
    }
  }
}

function makeFallbackDay() {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 512;
  const g = c.getContext("2d");
  const grd = g.createLinearGradient(0, 0, 0, 512);
  grd.addColorStop(0, "#9ecbff");
  grd.addColorStop(0.5, "#1a5f9e");
  grd.addColorStop(1, "#cfe8ff");
  g.fillStyle = grd;
  g.fillRect(0, 0, 1024, 512);
  g.fillStyle = "#2f8f5b";
  for (let i = 0; i < 40; i++) {
    g.beginPath();
    g.ellipse(
      Math.random() * 1024,
      80 + Math.random() * 350,
      40 + Math.random() * 90,
      18 + Math.random() * 40,
      0,
      0,
      Math.PI * 2,
    );
    g.fill();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function makeFallbackNight() {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 512;
  const g = c.getContext("2d");
  g.fillStyle = "#050814";
  g.fillRect(0, 0, 1024, 512);
  g.fillStyle = "#ffd89a";
  for (let i = 0; i < 1200; i++) {
    g.fillRect(Math.random() * 1024, Math.random() * 512, 1, 1);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function makeStars() {
  const n = 1800;
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const r = 18 + Math.random() * 10;
    const u = Math.random();
    const v = Math.random();
    const theta = 2 * Math.PI * u;
    const phi = Math.acos(2 * v - 1);
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.cos(phi);
    pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  return new THREE.Points(
    geo,
    new THREE.PointsMaterial({ color: 0xbfd4ff, size: 0.035, sizeAttenuation: true }),
  );
}

function makeDebris(color = 0xf07438) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.SphereGeometry(0.08, 20, 20),
    new THREE.MeshBasicMaterial({ color, toneMapped: false }),
  );
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(0.16, 16, 16),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.32,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  g.add(halo, body);
  g.renderOrder = 4;
  return g;
}

function makeIss() {
  const g = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.CylinderGeometry(0.018, 0.018, 0.08, 10),
    new THREE.MeshStandardMaterial({ color: 0xdfe7f2, metalness: 0.6, roughness: 0.3 }),
  );
  body.rotation.z = Math.PI / 2;
  const panelMat = new THREE.MeshStandardMaterial({
    color: 0x1a3d8f,
    emissive: 0x112244,
    metalness: 0.4,
    roughness: 0.35,
  });
  const panels = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.002, 0.05), panelMat);
  const panels2 = panels.clone();
  panels2.position.y = 0.03;
  panels.position.y = -0.03;
  g.add(body, panels, panels2);
  g.scale.setScalar(1.6);
  return g;
}

const earthVert = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const earthFrag = /* glsl */ `
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform vec2 sunLatLon;
  uniform float dayOnly;
  varying vec2 vUv;
  void main() {
    float lat = 90.0 - vUv.y * 180.0;
    float lon = vUv.x * 360.0 - 180.0;
    float d2r = 0.01745329251;
    float slat = sunLatLon.x * d2r;
    float slon = sunLatLon.y * d2r;
    float cosc = sin(lat * d2r) * sin(slat)
      + cos(lat * d2r) * cos(slat) * cos(lon * d2r - slon);
    float zenith = acos(clamp(cosc, -1.0, 1.0)) * 57.2957795;
    float mixF = mix(1.0 - smoothstep(90.0, 108.0, zenith), 1.0, dayOnly);
    vec3 dayTex = texture2D(dayMap, vUv).rgb;
    vec3 day = pow(max(dayTex, vec3(0.0)), vec3(0.86)) * 1.4;
    vec3 nightLights = texture2D(nightMap, vUv).rgb;
    vec3 night = dayTex * 0.32 + nightLights * 0.7;
    vec3 color = mix(night, day, mixF);
    gl_FragColor = vec4(color, 1.0);
  }
`;

export class Globe3D {
  constructor(container, { simple = false, layers = null, debrisVisible = null } = {}) {
    this.container = container;
    this.simple = simple;
    this.data = null;
    this.layers = { radiation: false, mmod: true, illumination: true, ...layers };
    this.debrisVisible = { ...debrisVisible };
    this.timeHours = 0;
    this.raf = 0;
    this.disposed = false;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x04060c);

    const w = container.clientWidth || 640;
    const h = container.clientHeight || 420;
    this.camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 80);
    this.camera.position.set(0.6, 1.1, 3.2);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h, false);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.minDistance = 1.7;
    this.controls.maxDistance = 6;
    this.controls.autoRotate = simple;
    this.controls.autoRotateSpeed = 0.35;

    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.scene.add(makeStars());
    this.scene.add(new THREE.AmbientLight(0x9bb4d0, 0.35));
    this.sunLight = new THREE.DirectionalLight(0xfff4e0, 1.4);
    this.scene.add(this.sunLight);

    this.iss = makeIss();
    this.root.add(this.iss);
    this.debrisGroup = new THREE.Group();
    this.debrisGroup.renderOrder = 2;
    this.root.add(this.debrisGroup);
    this.debrisMeshes = [];
    this.debrisEntries = [];
    this.mmodLink = null;

    this.trajGroup = new THREE.Group();
    this.trajLineMats = [];
    this.trajColorTargets = [];
    this.layerGroup = new THREE.Group();
    this.root.add(this.trajGroup, this.layerGroup);

    this.earthMat = new THREE.ShaderMaterial({
      uniforms: {
        dayMap: { value: null },
        nightMap: { value: null },
        sunLatLon: { value: new THREE.Vector2(0, 0) },
        dayOnly: { value: 0 },
      },
      vertexShader: earthVert,
      fragmentShader: earthFrag,
      toneMapped: false,
    });
    this.earth = new THREE.Mesh(new THREE.SphereGeometry(EARTH_RADIUS, 96, 96), this.earthMat);
    this.root.add(this.earth);

    const atmo = new THREE.Mesh(
      new THREE.SphereGeometry(EARTH_RADIUS * 1.045, 64, 64),
      new THREE.ShaderMaterial({
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false,
        uniforms: {},
        vertexShader: `varying vec3 vN; void main(){ vN = normalize(normalMatrix * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
        fragmentShader: `varying vec3 vN; void main(){ float i = pow(0.72 - dot(vN, vec3(0.0,0.0,1.0)), 3.2); gl_FragColor = vec4(0.35, 0.6, 1.0, 1.0) * i; }`,
      }),
    );
    this.root.add(atmo);

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(container);
    this.loop = this.loop.bind(this);
    this.raf = requestAnimationFrame(this.loop);
    this.loadTextures();
  }

  async loadTextures() {
    const [day, night] = await Promise.all([
      loadMap(TEXTURES.day, TEXTURES.dayCdn, THREE.SRGBColorSpace),
      loadMap(TEXTURES.night, TEXTURES.nightCdn, THREE.SRGBColorSpace),
    ]);
    if (this.disposed) {
      day?.dispose();
      night?.dispose();
      return;
    }
    this.earthMat.uniforms.dayMap.value = day || makeFallbackDay();
    this.earthMat.uniforms.nightMap.value = night || makeFallbackNight();
  }

  setData(data) {
    this.data = data;
    this.rebuildLayers();
  }

  setLayers(layers) {
    this.layers = { ...this.layers, ...layers };
    this.rebuildLayers();
  }

  setDebrisVisible(map) {
    this.debrisVisible = { ...map };
    this.applyDebrisVisible();
  }

  debrisOn(id) {
    return this.showMmod() && this.debrisVisible[id] !== false;
  }

  setTime(hours) {
    this.timeHours = hours;
    const iso = this.data?.request?.start ? addHoursIso(this.data.request.start, hours) : null;
    this.applyIllumination(iso);
    const pts = this.data?.orbit?.iss_trajectory;
    if (!pts?.length || !this.data) return;
    const start = Date.parse(this.data.request.start);
    const tMs = start + hours * 3600_000;
    const best = closestPoint(pts, tMs);
    const pos = latLonToVec3(best.lat, best.lon, best.alt_km);
    this.iss.position.copy(pos);
    const tangent = latLonToVec3(best.lat, best.lon + 2, best.alt_km).sub(pos).normalize();
    this.iss.lookAt(pos.clone().add(tangent));
    this.updateMmod(pos, tMs);
  }

  showIllumination() {
    return this.simple || this.layers.illumination;
  }

  applyIllumination(iso) {
    const live = this.showIllumination();
    const sub = subsolarPoint(iso);
    this.earthMat.uniforms.sunLatLon.value.set(sub.lat, sub.lon);
    this.earthMat.uniforms.dayOnly.value = live ? 0 : 1;
    const sun = sunDirectionFromIso(iso);
    this.sunLight.position.copy(sun.clone().multiplyScalar(8));
    this.recolorTrajectories(iso);
  }

  trajectoryColors(points, colorSun, colorShadow, iso) {
    const live = this.showIllumination();
    const sun = new THREE.Color(colorSun);
    const shadow = new THREE.Color(colorShadow);
    const colors = [];
    const mixed = new THREE.Color();
    for (const p of points) {
      const lit = live ? sunlitAmount(p.lat, p.lon, iso) : 1;
      mixed.copy(shadow).lerp(sun, lit);
      colors.push(mixed.r, mixed.g, mixed.b);
    }
    return colors;
  }

  recolorTrajectories(iso) {
    for (const track of this.trajColorTargets) {
      track.geo.setColors(this.trajectoryColors(track.points, track.colorSun, track.colorShadow, iso));
    }
  }

  updateMmod(issPos, tMs) {
    const debris = this.data?.orbit?.debris || [];
    let nearestPos = null;
    let nearestKm = Infinity;
    this.debrisMeshes.forEach((mesh, i) => {
      const track = debris[i];
      const id = debrisTrackId(track, i);
      const pts = track?.points;
      if (!this.debrisOn(id) || !pts?.length) {
        mesh.visible = false;
        return;
      }
      const dp = closestPoint(pts, tMs);
      const dpos = latLonToVec3(dp.lat, dp.lon, dp.alt_km);
      mesh.visible = true;
      mesh.position.copy(dpos);
      const km = dp.distance_km ?? nearestKm;
      if (km < nearestKm) {
        nearestKm = km;
        nearestPos = dpos;
      }
    });
    this.setMmodLink(nearestPos ? issPos : null, nearestPos);
  }

  setMmodLink(from, to) {
    if (!this.mmodLink) return;
    const on = Boolean(from && to);
    this.mmodLink.visible = on;
    if (!on) return;
    const pos = this.mmodLink.geometry.attributes.position;
    pos.setXYZ(0, from.x, from.y, from.z);
    pos.setXYZ(1, to.x, to.y, to.z);
    pos.needsUpdate = true;
    this.mmodLink.geometry.computeBoundingSphere();
  }

  addTrajectoryLine(points, colorSun, colorShadow) {
    if (!points?.length || points.length < 2) return;
    const positions = [];
    for (const p of points) {
      const v = latLonToVec3(p.lat, p.lon, p.alt_km);
      positions.push(v.x, v.y, v.z);
    }
    const iso = this.data?.request?.start ? addHoursIso(this.data.request.start, this.timeHours) : null;
    const colors = this.trajectoryColors(points, colorSun, colorShadow, iso);
    const geo = new LineGeometry();
    geo.setPositions(positions);
    geo.setColors(colors);
    const w = this.container.clientWidth || 640;
    const h = this.container.clientHeight || 420;
    const under = new LineMaterial({
      color: 0x1c1c1c,
      linewidth: 5,
      transparent: true,
      opacity: 0.35,
      worldUnits: false,
      toneMapped: false,
    });
    const mat = new LineMaterial({
      vertexColors: true,
      linewidth: 2.8,
      worldUnits: false,
      toneMapped: false,
    });
    under.resolution.set(w, h);
    mat.resolution.set(w, h);
    this.trajLineMats.push(under, mat);
    const underLine = new Line2(geo, under);
    const line = new Line2(geo, mat);
    underLine.computeLineDistances();
    line.computeLineDistances();
    this.trajGroup.add(underLine, line);
    this.trajColorTargets.push({ points, geo, colorSun, colorShadow });
  }

  addMmodLine(points, colorHex, parent = this.debrisGroup) {
    if (!points?.length || points.length < 2) return;
    const positions = [];
    for (const p of points) {
      const v = latLonToVec3(p.lat, p.lon, p.alt_km);
      positions.push(v.x, v.y, v.z);
    }
    const geo = new LineGeometry();
    geo.setPositions(positions);
    const w = this.container.clientWidth || 640;
    const h = this.container.clientHeight || 420;
    const under = new LineMaterial({
      color: 0x1c1c1c,
      linewidth: 7,
      transparent: true,
      opacity: 0.45,
      worldUnits: false,
      toneMapped: false,
    });
    const mat = new LineMaterial({
      color: colorHex,
      linewidth: 4.6,
      worldUnits: false,
      toneMapped: false,
    });
    under.resolution.set(w, h);
    mat.resolution.set(w, h);
    this.trajLineMats.push(under, mat);
    const underLine = new Line2(geo, under);
    const line = new Line2(geo, mat);
    underLine.computeLineDistances();
    line.computeLineDistances();
    underLine.renderOrder = 2;
    line.renderOrder = 3;
    parent.add(underLine, line);
  }

  rebuildTrajectory() {
    this.trajGroup.clear();
    this.trajLineMats = [];
    this.trajColorTargets = [];
    this.debrisGroup.clear();
    this.debrisMeshes = [];
    this.debrisEntries = [];
    this.mmodLink = null;
    const pts = this.data?.orbit?.iss_trajectory;
    if (!pts?.length) return;
    this.addTrajectoryLine(pts, 0x3ee0c8, 0x6b7cff);

    if (!this.showMmod()) return;
    const debrisColors = [0xf07438, 0xe0b14a, 0xe34732, 0xd45a22];
    const debris = this.data.orbit.debris || [];
    debris.forEach((track, i) => {
      const id = debrisTrackId(track, i);
      const color = debrisColors[i % debrisColors.length];
      const group = new THREE.Group();
      group.userData.debrisId = id;
      this.addMmodLine(track.points, color, group);
      const mesh = makeDebris(color);
      group.add(mesh);
      this.debrisGroup.add(group);
      this.debrisMeshes.push(mesh);
      this.debrisEntries.push({ id, group });
    });
    const linkGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    this.mmodLink = new THREE.Line(
      linkGeo,
      new THREE.LineBasicMaterial({ color: 0xf07438, transparent: true, opacity: 0.95 }),
    );
    this.mmodLink.visible = false;
    this.debrisGroup.add(this.mmodLink);
    this.applyDebrisVisible();
  }

  applyDebrisVisible() {
    for (const entry of this.debrisEntries) {
      entry.group.visible = this.debrisOn(entry.id);
    }
  }

  showMmod() {
    return this.simple || this.layers.mmod;
  }

  rebuildLayers() {
    this.layerGroup.clear();
    this.rebuildTrajectory();
    this.setTime(this.timeHours);
    if (this.simple || !this.data) return;

    if (this.layers.radiation) {
      const saa = this.makeRegion(-26, -45, 0xff5c6c, 0.22);
      this.layerGroup.add(saa);
    }
  }

  makeRegion(lat, lon, color, opacity) {
    const g = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const offsets = [
      [0, 0],
      [-8, 10],
      [6, -12],
      [-12, -8],
      [10, 14],
      [-4, 20],
      [8, -6],
      [-16, 4],
    ];
    for (const [dLat, dLon] of offsets) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 16), mat);
      m.position.copy(latLonToVec3(lat + dLat, lon + dLon, 30));
      g.add(m);
    }
    return g;
  }

  resize() {
    if (!this.container) return;
    const w = this.container.clientWidth || 1;
    const h = this.container.clientHeight || 1;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    for (const mat of this.trajLineMats) mat.resolution.set(w, h);
  }

  loop() {
    if (this.disposed) return;
    this.controls.update();
    this.iss.rotateX(0.01);
    this.renderer.render(this.scene, this.camera);
    this.raf = requestAnimationFrame(this.loop);
  }

  getCameraState() {
    return {
      position: this.camera.position.clone(),
      target: this.controls.target.clone(),
    };
  }

  setCameraState(state) {
    if (!state?.position || !state?.target) return;
    this.camera.position.copy(state.position);
    this.controls.target.copy(state.target);
    this.controls.update();
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.controls.dispose();
    this.scene.traverse((obj) => {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        for (const m of mats) {
          for (const key of Object.keys(m)) {
            if (m[key]?.isTexture) m[key].dispose();
          }
          m.dispose();
        }
      }
    });
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
