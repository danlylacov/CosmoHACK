import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";

const EARTH_KM = 6371;
const EARTH_DAY = new URL("./assets/earth-day.jpg", import.meta.url).href;
const EARTH_NORMAL = new URL("./assets/earth-normal.jpg", import.meta.url).href;

const PALETTE = ["#8fd3e8", "#e09a4a", "#d45d4a", "#b39bc9"];
const SPEED = 360;

const ATMOSPHERE_VERT = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vViewDirection;

  void main() {
    vec4 worldPos = modelMatrix * vec4(position, 1.0);
    vViewDirection = normalize(cameraPosition - worldPos.xyz);
    vNormal = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const ATMOSPHERE_FRAG = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vViewDirection;

  void main() {
    vec3 n = normalize(vNormal);
    vec3 v = normalize(vViewDirection);
    float fresnel = pow(1.0 - abs(dot(n, v)), 2.4);
    vec3 color = vec3(0.42, 0.68, 1.0);
    gl_FragColor = vec4(color * fresnel, fresnel * 0.9);
  }
`;

function latLonAltToVec3(lat, lon, altKm) {
  const r = 1 + altKm / EARTH_KM;
  const phi = THREE.MathUtils.degToRad(90 - lat);
  const theta = THREE.MathUtils.degToRad(lon + 180);
  const sinPhi = Math.sin(phi);
  return new THREE.Vector3(
    -r * sinPhi * Math.cos(theta),
    r * Math.cos(phi),
    r * sinPhi * Math.sin(theta),
  );
}

function parseUtc(value) {
  return Date.parse(String(value).replace(" ", "T") + "Z");
}

function formatUtc(ms) {
  if (!Number.isFinite(ms)) return "—";
  return new Date(ms).toISOString().replace("T", " ").replace(".000Z", " UTC");
}

function lerpVec(a, b, t, out) {
  out.x = a.x + (b.x - a.x) * t;
  out.y = a.y + (b.y - a.y) * t;
  out.z = a.z + (b.z - a.z) * t;
  return out;
}

function lerpLon(a, b, t) {
  let d = b - a;
  if (d > 180) d -= 360;
  if (d < -180) d += 360;
  return a + d * t;
}

function sampleTrack(points, timeMs, out) {
  const last = points.length - 1;
  if (timeMs <= points[0].t) {
    out.copy(points[0].pos);
    return { lat: points[0].lat, lon: points[0].lon, alt: points[0].alt };
  }
  if (timeMs >= points[last].t) {
    out.copy(points[last].pos);
    return { lat: points[last].lat, lon: points[last].lon, alt: points[last].alt };
  }
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= timeMs) lo = mid;
    else hi = mid;
  }
  const span = points[hi].t - points[lo].t || 1;
  const a = (timeMs - points[lo].t) / span;
  lerpVec(points[lo].pos, points[hi].pos, a, out);
  return {
    lat: points[lo].lat + (points[hi].lat - points[lo].lat) * a,
    lon: lerpLon(points[lo].lon, points[hi].lon, a),
    alt: points[lo].alt + (points[hi].alt - points[lo].alt) * a,
  };
}

function prefersReducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

class CosmoGlobe extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._objects = [];
    this._selected = null;
    this._playing = !prefersReducedMotion();
    this._t0 = 0;
    this._t1 = 1;
    this._time = 0;
    this._scratch = new THREE.Vector3();
    this._pointer = new THREE.Vector2();
    this._drag = { x: 0, y: 0, moved: false };
    this._raf = 0;
    this._disposed = false;
  }

  static get observedAttributes() {
    return ["src"];
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${new URL("./globe.css", import.meta.url)}">
      <div class="stage"></div>
      <div class="hud">
        <div class="frame" aria-hidden="true"></div>
        <div class="brand">
          <h1>Земля и орбиты</h1>
          <p>МКС и тела на сближении. Вращайте сцену, приближайте колесом.</p>
        </div>
        <div class="catalog" role="list" aria-label="Каталог объектов"></div>
        <aside class="detail" hidden></aside>
        <div class="clock">
          <button type="button" class="play" aria-pressed="true">Пауза</button>
          <input type="range" min="0" max="1000" value="0" aria-label="Время вдоль траектории" />
          <div class="stamp">загрузка</div>
        </div>
        <div class="hint">ЛКМ — вращение, колесо — масштаб, клик по маркеру — карточка</div>
        <div class="status">Собираю сцену…</div>
      </div>
    `;

    this._stage = this.shadowRoot.querySelector(".stage");
    this._catalog = this.shadowRoot.querySelector(".catalog");
    this._detail = this.shadowRoot.querySelector(".detail");
    this._status = this.shadowRoot.querySelector(".status");
    this._playBtn = this.shadowRoot.querySelector(".play");
    this._slider = this.shadowRoot.querySelector("input[type='range']");
    this._stamp = this.shadowRoot.querySelector(".stamp");

    this._initScene();
    this._bindUi();
    this._load(this.getAttribute("src") || "../MMOD/trajectories.json");
    this._loop = this._loop.bind(this);
    this._raf = requestAnimationFrame(this._loop);
  }

  disconnectedCallback() {
    this._disposed = true;
    cancelAnimationFrame(this._raf);
    this._ro?.disconnect();
    window.removeEventListener("keydown", this._onKey);
    this._renderer?.dispose();
    this._earthMat?.map?.dispose();
    this._earthMat?.normalMap?.dispose();
    this._earthMat?.dispose();
    this._starGeo?.dispose();
    this._starMat?.dispose();
    this._controls?.dispose();
    for (const obj of this._objects) {
      obj.line.geometry.dispose();
      obj.line.material.dispose();
      obj.marker.geometry.dispose();
      obj.marker.material.dispose();
    }
  }

  attributeChangedCallback(name, prev, next) {
    if (name === "src" && prev !== next && this.isConnected && next) {
      this._load(next);
    }
  }

  _initScene() {
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0c1220);
    this._scene = scene;

    const camera = new THREE.PerspectiveCamera(45, 1, 0.05, 40);
    camera.position.set(0.15, 0.35, 3.45);
    scene.add(camera);
    this._camera = camera;

    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = false;
    this._stage.appendChild(renderer.domElement);
    this._renderer = renderer;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.enablePan = false;
    controls.minDistance = 1.18;
    controls.maxDistance = 7.5;
    controls.target.set(0, 0, 0);
    controls.update();
    this._controls = controls;

    scene.add(new THREE.AmbientLight(0x7f8ba3, 0.55));
    const hemi = new THREE.HemisphereLight(0x9ec5ff, 0x1a140c, 0.45);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xfff4e5, 2.4);
    sun.position.set(6, 2.2, 3.4);
    scene.add(sun);

    this._addStars();
    this._addEarth();

    this._raycaster = new THREE.Raycaster();
    this._clock = new THREE.Clock();

    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(this);
    this._resize();

    const canvas = renderer.domElement;
    canvas.setAttribute("aria-label", "Интерактивный 3D глобус Земли с орбитами");
    canvas.addEventListener("pointerdown", (e) => {
      this._drag = { x: e.clientX, y: e.clientY, moved: false };
    });
    canvas.addEventListener("pointermove", (e) => {
      if (Math.hypot(e.clientX - this._drag.x, e.clientY - this._drag.y) > 4) {
        this._drag.moved = true;
      }
    });
    canvas.addEventListener("pointerup", (e) => {
      if (!this._drag.moved) this._pick(e);
    });
  }

  _addStars() {
    const count = 2800;
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const r = 12 + Math.random() * 10;
      const u = Math.random();
      const v = Math.random();
      const theta = 2 * Math.PI * u;
      const phi = Math.acos(2 * v - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.cos(phi);
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      color: 0xd9deea,
      size: 0.025,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
    });
    this._starGeo = geo;
    this._starMat = mat;
    this._scene.add(new THREE.Points(geo, mat));
  }

  async _addEarth() {
    const geo = new THREE.SphereGeometry(1, 96, 64);
    const fallback = new THREE.MeshStandardMaterial({
      color: 0x1d4f8a,
      roughness: 0.85,
      metalness: 0.05,
    });
    const earth = new THREE.Mesh(geo, fallback);
    this._earth = earth;
    this._earthMat = fallback;
    this._scene.add(earth);

    const atmosphere = new THREE.Mesh(
      new THREE.SphereGeometry(1.045, 64, 48),
      new THREE.ShaderMaterial({
        vertexShader: ATMOSPHERE_VERT,
        fragmentShader: ATMOSPHERE_FRAG,
        blending: THREE.AdditiveBlending,
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false,
      }),
    );
    this._scene.add(atmosphere);

    try {
      const loader = new THREE.TextureLoader();
      loader.setCrossOrigin("anonymous");
      const [day, normal] = await Promise.all([
        loader.loadAsync(EARTH_DAY),
        loader.loadAsync(EARTH_NORMAL),
      ]);
      if (this._disposed) return;
      day.colorSpace = THREE.SRGBColorSpace;
      day.anisotropy = this._renderer.capabilities.getMaxAnisotropy();
      normal.anisotropy = day.anisotropy;
      const mat = new THREE.MeshStandardMaterial({
        map: day,
        normalMap: normal,
        roughness: 0.82,
        metalness: 0.04,
      });
      earth.material = mat;
      fallback.dispose();
      this._earthMat = mat;
    } catch {
      // Keep the solid-color Earth if the public textures are blocked.
    }
  }

  _bindUi() {
    this._playBtn.addEventListener("click", () => {
      this._playing = !this._playing;
      this._syncPlayButton();
    });
    this._slider.addEventListener("input", () => {
      const u = Number(this._slider.value) / 1000;
      this._time = this._t0 + u * (this._t1 - this._t0);
      this._playing = false;
      this._syncPlayButton();
      this._updateMarkers(true);
    });
    this._onKey = (e) => {
      if (e.code === "Space" && e.target === document.body) {
        e.preventDefault();
        this._playing = !this._playing;
        this._syncPlayButton();
      }
    };
    window.addEventListener("keydown", this._onKey);
    this._syncPlayButton();
  }

  _syncPlayButton() {
    this._playBtn.textContent = this._playing ? "Пауза" : "Играть";
    this._playBtn.setAttribute("aria-pressed", String(this._playing));
  }

  async _load(src) {
    this._status.hidden = false;
    this._status.textContent = "Загружаю траектории…";
    try {
      const url = new URL(src, import.meta.url);
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this._applyData(data);
      this._status.hidden = true;
    } catch (err) {
      this._status.hidden = false;
      this._status.textContent = `Не удалось прочитать траектории: ${err.message}. Откройте страницу через локальный сервер.`;
    }
  }

  _clearObjects() {
    for (const obj of this._objects) {
      this._scene.remove(obj.line);
      this._scene.remove(obj.marker);
      obj.line.geometry.dispose();
      obj.line.material.dispose();
      obj.marker.geometry.dispose();
      obj.marker.material.dispose();
    }
    this._objects = [];
    this._catalog.replaceChildren();
  }

  _applyData(data) {
    this._clearObjects();
    const records = Array.isArray(data.objects) ? data.objects : [];
    this._t0 = parseUtc(data.t0_utc) || parseUtc(records[0]?.points?.[0]?.time_utc);
    this._t1 = parseUtc(data.t1_utc) || this._t0 + 1;
    this._time = this._t0;

    records.forEach((record, index) => {
      const color = new THREE.Color(PALETTE[index % PALETTE.length]);
      const points = (record.points || []).map((p) => ({
        t: parseUtc(p.time_utc),
        lat: p.lat,
        lon: p.lon,
        alt: p.alt_km,
        pos: latLonAltToVec3(p.lat, p.lon, p.alt_km),
      }));
      if (points.length < 2) return;

      const positions = [];
      for (const p of points) positions.push(p.pos.x, p.pos.y, p.pos.z);
      const geom = new LineGeometry();
      geom.setPositions(positions);
      const mat = new LineMaterial({
        color,
        linewidth: record.role === "iss" ? 2.6 : 1.7,
        transparent: true,
        opacity: record.role === "iss" ? 0.95 : 0.78,
        worldUnits: false,
      });
      mat.resolution.set(this.clientWidth || 1, this.clientHeight || 1);
      const line = new Line2(geom, mat);
      line.computeLineDistances();
      this._scene.add(line);

      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(record.role === "iss" ? 0.018 : 0.014, 16, 12),
        new THREE.MeshBasicMaterial({ color }),
      );
      marker.userData.norad = record.norad;
      this._scene.add(marker);

      const item = {
        record,
        color,
        points,
        line,
        marker,
        visible: true,
      };
      this._objects.push(item);
    });

    this._renderCatalog();
    if (this._objects.length) this._select(this._objects[0]);
    this._updateMarkers(true);
    this._resize();
  }

  _renderCatalog() {
    this._catalog.replaceChildren();
    for (const obj of this._objects) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "listitem");
      btn.style.setProperty("--swatch", `#${obj.color.getHexString()}`);
      btn.innerHTML = `
        <span class="swatch" aria-hidden="true"></span>
        <span class="obj-name">
          <strong>${obj.record.name}</strong>
          <span>NORAD ${obj.record.norad}</span>
        </span>
        <span class="obj-meta">${obj.record.role === "iss" ? "МКС" : "сближение"}</span>
      `;
      btn.addEventListener("click", () => {
        if (this._selected === obj && obj.visible) {
          obj.visible = false;
        } else {
          obj.visible = true;
          this._select(obj);
        }
        obj.line.visible = obj.visible;
        obj.marker.visible = obj.visible;
        this._renderCatalog();
        this._renderDetail();
      });
      if (this._selected === obj) btn.classList.add("is-selected");
      if (!obj.visible) btn.classList.add("is-off");
      this._catalog.appendChild(btn);
    }
  }

  _select(obj) {
    this._selected = obj;
    this._renderCatalog();
    this._renderDetail();
  }

  _renderDetail() {
    const obj = this._selected;
    if (!obj) {
      this._detail.hidden = true;
      return;
    }
    const sample = obj._sample || sampleTrack(obj.points, this._time, this._scratch);
    const rows = [
      ["NORAD", obj.record.norad],
      ["Роль", obj.record.role === "iss" ? "МКС" : "объект сближения"],
      ["Высота", `${sample.alt.toFixed(1)} км`],
      ["Широта", `${sample.lat.toFixed(2)}°`],
      ["Долгота", `${sample.lon.toFixed(2)}°`],
    ];
    if (obj.record.tca_utc) rows.push(["TCA", obj.record.tca_utc]);
    if (obj.record.min_range_km) {
      rows.push(["Мин. дистанция", `${obj.record.min_range_km} км`]);
    }
    this._detail.hidden = false;
    this._detail.innerHTML = `
      <h2>${obj.record.name}</h2>
      <dl>
        ${rows
          .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
          .join("")}
      </dl>
    `;
  }

  _pick(event) {
    const rect = this._renderer.domElement.getBoundingClientRect();
    this._pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this._pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this._raycaster.setFromCamera(this._pointer, this._camera);
    const hits = this._raycaster.intersectObjects(
      this._objects.filter((o) => o.visible).map((o) => o.marker),
    );
    if (!hits.length) return;
    const norad = hits[0].object.userData.norad;
    const obj = this._objects.find((o) => o.record.norad === norad);
    if (obj) this._select(obj);
  }

  _resize() {
    const width = Math.max(1, this.clientWidth);
    const height = Math.max(1, this.clientHeight);
    this._camera.aspect = width / height;
    this._camera.updateProjectionMatrix();
    this._renderer.setSize(width, height, false);
    this._renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    for (const obj of this._objects) {
      obj.line.material.resolution.set(width, height);
    }
  }

  _updateMarkers(refreshDetail) {
    for (const obj of this._objects) {
      const sample = sampleTrack(obj.points, this._time, obj.marker.position);
      obj._sample = sample;
    }
    const u = (this._time - this._t0) / (this._t1 - this._t0 || 1);
    this._slider.value = String(Math.round(THREE.MathUtils.clamp(u, 0, 1) * 1000));
    this._stamp.textContent = formatUtc(this._time);
    if (refreshDetail) this._renderDetail();
  }

  _loop() {
    if (this._disposed) return;
    this._raf = requestAnimationFrame(this._loop);
    const delta = this._clock.getDelta();
    if (this._playing && this._objects.length) {
      this._time += delta * SPEED * 1000;
      if (this._time >= this._t1) this._time = this._t0;
      const second = Math.floor(this._time / 1000);
      this._updateMarkers(second !== this._detailSecond);
      this._detailSecond = second;
    }
    this._controls.update();
    this._renderer.render(this._scene, this._camera);
  }
}

if (!customElements.get("cosmo-globe")) {
  customElements.define("cosmo-globe", CosmoGlobe);
}

export { CosmoGlobe };
