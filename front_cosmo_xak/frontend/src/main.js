import "./styles/main.css";
import { USE_MOCKS, CURRENT_POLL_MS } from "./config.js";
import { api } from "./api/client.js";
import { store, syncViewFromHash } from "./lib/store.js";
import { validateQuery, defaultQuery, toAnalyzeRequest } from "./lib/validate.js";
import { applyControlsToRequest } from "./lib/sources.js";
import { applyOrbitPositions, applyConjunctionDistances, formatChartNow, chartDotXY, toOrbitWindow, syncDebrisVisible, playbackStopHours, stepPlayback, CRITICAL_DISTANCE_KM } from "./lib/orbits.js";
import { applyEvaWindows, toEvaWindowsRequest } from "./lib/eva.js";
import { applyForecast, toForecastRequest } from "./lib/forecast.js";
import { finalizeLive, patchLiveHealth } from "./lib/live.js";
import { shellHTML } from "./lib/ui.js";
import { scrubberLabel } from "./lib/format.js";
import { renderSimpleView } from "./components/SimpleView.js";
import { renderAdvancedView } from "./components/AdvancedView.js";
import { readQueryForm } from "./components/QueryForm.js";
import { runExport } from "./components/ExportPanel.js";
import { Globe3D } from "./components/Globe3D.js";

const UI_KEYS = [
  "view",
  "loadStatus",
  "data",
  "errorMessage",
  "query",
  "errors",
  "sourcesLoadStatus",
  "tle",
  "previousData",
];

let globe = null;
let lastQuery = store.get().query;
let pollTimer = null;
let timeRaf = 0;
let lastTick = 0;

function maxGlobeHours(data) {
  return Math.max(1, Number(data?.request?.period) || 0);
}

function stopTimeLoop() {
  if (!timeRaf) return;
  cancelAnimationFrame(timeRaf);
  timeRaf = 0;
}

function startTimeLoop() {
  if (timeRaf) return;
  lastTick = performance.now();
  const step = (now) => {
    timeRaf = requestAnimationFrame(step);
    const s = store.get();
    if (!s.timePlaying || !s.data) {
      lastTick = now;
      return;
    }
    const dt = Math.min(0.08, Math.max(0, (now - lastTick) / 1000));
    lastTick = now;
    const maxT = maxGlobeHours(s.data);
    const speed = Number(s.timeSpeed) || 1;
    const next = stepPlayback(s.globeTime, dt * speed, playbackStopHours(s.data), maxT);
    if (next.pause) {
      store.set({ globeTime: next.t, timePlaying: false, timePauseReason: next.reason });
      return;
    }
    store.set({ globeTime: next.t });
  };
  timeRaf = requestAnimationFrame(step);
}

function syncTimeLoop(state) {
  if (state.timePlaying && state.data && state.view === "advanced") startTimeLoop();
  else stopTimeLoop();
}

function uiChanged(state, prev) {
  if (!prev) return true;
  return UI_KEYS.some((k) => state[k] !== prev[k]);
}

function currentRequest(query = store.get().query, controls = store.get().sourceControls) {
  return toAnalyzeRequest(applyControlsToRequest(query, controls));
}

async function enableMocks() {
  if (!USE_MOCKS) return;
  const { worker } = await import("./mocks/browser.js");
  await worker.start({
    serviceWorker: { url: "/mockServiceWorker.js" },
    onUnhandledRequest: "bypass",
  });
}

async function analyze(query) {
  const merged = applyControlsToRequest(query, store.get().sourceControls);
  lastQuery = merged;
  const errors = validateQuery(merged);
  if (Object.keys(errors).length) {
    store.set({ query: merged, errors, loadStatus: "idle" });
    return;
  }
  const previousQuery = store.get().data ? store.get().query : null;
  const previousData = store.get().data;
  store.set({ query: merged, errors: {}, loadStatus: "loading", errorMessage: null, errorCode: null, timePlaying: false });
  try {
    const orbitWindow = toOrbitWindow(merged);
    const critical_distance_km = Number(merged.critical_distance_km) || CRITICAL_DISTANCE_KM;
    const [analyzed, positions, distances, evaSettled, forecastSettled] = await Promise.all([
      api.analyze(currentRequest(merged)),
      api.getOrbitPositions(orbitWindow),
      api.getConjunctionDistances({
        ...orbitWindow,
        critical_distance_km,
      }),
      api
        .getEvaWindows(toEvaWindowsRequest(merged))
        .then((payload) => ({ ok: true, payload }))
        .catch((error) => ({ ok: false, error })),
      api
        .getForecast(toForecastRequest(merged))
        .then((payload) => ({ ok: true, payload }))
        .catch((error) => ({ ok: false, error })),
    ]);
    const data = finalizeLive(
      applyForecast(
        applyEvaWindows(
          applyConjunctionDistances(
            applyOrbitPositions(
              { ...analyzed, request: { ...analyzed.request, critical_distance_km } },
              positions,
            ),
            distances ? { ...distances, request: { ...distances.request, critical_distance_km } } : distances,
          ),
          evaSettled.ok ? evaSettled.payload : null,
          evaSettled.ok ? null : evaSettled.error,
        ),
        forecastSettled.ok ? forecastSettled.payload : null,
        forecastSettled.ok ? null : forecastSettled.error,
      ),
    );
    const epoch = positions?.data_quality?.elements_epoch || data.orbit?.tle_epoch;
    const tle = {
      status: "success",
      name: "ISS (ZARYA)",
      norad_id: 25544,
      source: positions?.source?.name || data.orbit?.tle_source || null,
      epoch: epoch || null,
      age_hours: data.orbit?.tle_age_hours ?? null,
      line1: null,
      line2: null,
    };
    store.set({
      data,
      tle,
      previousQuery,
      previousData,
      loadStatus: data.status === "partial" ? "partial" : "success",
      globeTime: 0,
      timePlaying: false,
      timePauseReason: null,
      sources: { sources: data.metadata.sources },
      debrisVisible: syncDebrisVisible(data.orbit?.debris, store.get().debrisVisible),
    });
    syncPolling(store.get());
  } catch (err) {
    const stale = err.code === "STALE_ORBITAL_ELEMENTS" || /stale|epoch/i.test(err.message || "");
    store.set({
      loadStatus: "error",
      errorMessage: stale
        ? `${err.message} Для живой орбиты укажите дату около текущей эпохи TLE — режим «Сейчас».`
        : err.message,
      errorCode: err.code,
    });
  }
}

async function refreshSources() {
  store.set({ sourcesLoadStatus: "loading" });
  await analyze(store.get().query);
  const s = store.get();
  store.set({
    sourcesLoadStatus: s.loadStatus === "error" ? "error" : "success",
  });
}

async function pollCurrent() {
  const s = store.get();
  if (s.query.mode !== "current" || s.loadStatus === "loading") return;
  try {
    const health = await api.getLiveHealth();
    const current = store.get().data;
    store.set({
      autoRefreshNote: `автопроверка ${new Date().toISOString().slice(11, 16)} UTC`,
      data: current ? patchLiveHealth(current, health) : current,
    });
  } catch {
    store.set({ autoRefreshNote: "автопроверка не удалась, прежние данные сохранены" });
  }
}

function syncPolling(state) {
  clearInterval(pollTimer);
  pollTimer = null;
  if (state.query.mode !== "current") {
    if (state.autoRefreshNote) store.set({ autoRefreshNote: null });
    return;
  }
  pollTimer = setInterval(pollCurrent, CURRENT_POLL_MS);
}

function bindForm(root) {
  const form = root.querySelector("[data-form=query]");
  if (!form) return;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    analyze(readQueryForm(form, store.get().query));
  });
  form.querySelectorAll("input[name=mode]").forEach((input) => {
    input.addEventListener("change", () => {
      const mode = form.querySelector("input[name=mode]:checked")?.value;
      if (mode && mode !== store.get().query.mode) {
        const nextQuery = defaultQuery(mode);
        const typed = Number(
          String(form.querySelector("[name=critical_distance_km]")?.value || "").trim().replace(",", "."),
        );
        store.set({
          query: {
            ...nextQuery,
            critical_distance_km: Number.isFinite(typed) ? typed : nextQuery.critical_distance_km,
          },
          errors: {},
          autoRefreshNote: null,
        });
        syncPolling(store.get());
      }
    });
  });
  form.querySelectorAll("input[name=historical_intent]").forEach((input) => {
    input.addEventListener("change", () => {
      const intent = form.querySelector("input[name=historical_intent]:checked")?.value;
      const q = store.get().query;
      store.patchQuery({
        historical_intent: intent,
        cutoff_time: intent === "replay" ? q.start : null,
      });
    });
  });
}

function bindActions(root) {
  root.querySelector("[data-action=retry]")?.addEventListener("click", () => analyze(lastQuery));
  root.querySelectorAll("[data-action=refresh-sources]").forEach((btn) => {
    btn.addEventListener("click", () => refreshSources());
  });
  root.querySelectorAll("[data-export]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await runExport(btn.dataset.export, store.get().data);
      } catch (err) {
        window.alert(err.message || "Не удалось выгрузить файл.");
      } finally {
        btn.disabled = false;
      }
    });
  });
  root.querySelectorAll("[data-source-flag]").forEach((input) => {
    input.addEventListener("change", () => {
      const name = input.dataset.sourceName;
      const flag = input.dataset.sourceFlag;
      const current = { ...(store.get().sourceControls[name] || {}) };
      current[flag] = input.checked;
      const sourceControls = { ...store.get().sourceControls, [name]: current };
      store.set({ sourceControls });
      if (store.get().data) analyze({ ...store.get().query });
    });
  });
}

function hoursFromChartPointer(plot, event, maxT) {
  const rect = plot.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const u = Math.min(1, Math.max(0, x / (rect.width || 1)));
  return u * Number(maxT || 0);
}

function bindGlobeControls(root, state) {
  root.querySelectorAll("[data-scrubber]").forEach((range) => {
    range.addEventListener("input", () => {
      store.set({ globeTime: Number(range.value), timePauseReason: null });
    });
  });
  root.querySelectorAll("[data-chart-scrub]").forEach((plot) => {
    const maxT = () =>
      Number(plot.closest("[data-chart-max]")?.dataset.chartMax) || maxGlobeHours(store.get().data);
    const scrub = (event) => {
      store.set({
        globeTime: hoursFromChartPointer(plot, event, maxT()),
        timePlaying: false,
        timePauseReason: null,
      });
    };
    plot.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      plot.setPointerCapture(event.pointerId);
      scrub(event);
    });
    plot.addEventListener("pointermove", (event) => {
      if (!plot.hasPointerCapture(event.pointerId)) return;
      scrub(event);
    });
  });
  root.querySelectorAll("[data-time-play]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const s = store.get();
      if (s.timePlaying) {
        store.set({ timePlaying: false, timePauseReason: null });
        return;
      }
      const maxT = maxGlobeHours(s.data);
      const atEnd = Number(s.globeTime) >= maxT - 1e-6;
      store.set({
        timePlaying: true,
        timePauseReason: null,
        globeTime: atEnd ? 0 : s.globeTime,
      });
    });
  });
  root.querySelectorAll("[data-time-speed]").forEach((sel) => {
    sel.addEventListener("change", () => {
      store.set({ timeSpeed: Number(sel.value) || 1 });
    });
  });
  root.querySelectorAll("[data-layer]").forEach((input) => {
    input.addEventListener("change", () => {
      store.set({
        layers: { ...store.get().layers, [input.dataset.layer]: input.checked },
      });
    });
  });
  root.querySelectorAll("[data-debris-id]").forEach((input) => {
    input.addEventListener("change", () => {
      store.set({
        debrisVisible: { ...store.get().debrisVisible, [input.dataset.debrisId]: input.checked },
      });
    });
  });
}

function mountGlobe(root, state) {
  const cameraState = globe && !globe.disposed ? globe.getCameraState() : null;
  if (globe) {
    globe.dispose();
    globe = null;
  }
  const host = root.querySelector("[data-globe]");
  if (!host || !state.data) return;
  globe = new Globe3D(host, {
    simple: state.view === "simple",
    layers: state.layers,
    debrisVisible: state.debrisVisible,
  });
  globe.setData(state.data);
  globe.setTime(state.globeTime);
  if (cameraState) globe.setCameraState(cameraState);
}

function patchCharts(state) {
  const data = state.data;
  const host = document.querySelector("[data-chart-max]");
  if (!data || !host) return;
  const maxT = Number(host.dataset.chartMax) || 1;
  const t = state.globeTime;
  const sw = document.querySelector('[data-chart-now="sw"]');
  const mm = document.querySelector('[data-chart-now="mmod"]');
  if (sw) sw.textContent = formatChartNow("sw", data.risk_sw.time_series, t);
  if (mm) mm.textContent = formatChartNow("mmod", data.risk_mmod.time_series, t);
  document.querySelectorAll("[data-chart-dot]").forEach((el) => {
    const chart = el.closest(".chart");
    const key = chart?.classList.contains("chart--mmod") ? "mmod" : "sw";
    const series = key === "mmod" ? data.risk_mmod.time_series : data.risk_sw.time_series;
    const pt = chartDotXY(series, t, maxT);
    el.setAttribute("cx", String(pt.x));
    el.setAttribute("cy", String(pt.y));
  });
  const label = scrubberLabel(data.request.start, t);
  document.querySelectorAll("[data-scrubber]").forEach((el) => {
    if (el.value !== String(t)) el.value = String(t);
    el.setAttribute("aria-valuetext", label);
  });
  document.querySelectorAll("[data-scrubber-label]").forEach((el) => {
    el.textContent = label;
  });
}

function patchTimeControls(state) {
  const playing = Boolean(state.timePlaying);
  document.querySelectorAll("[data-time-play]").forEach((btn) => {
    btn.setAttribute("aria-pressed", playing ? "true" : "false");
    btn.setAttribute("aria-label", playing ? "Остановить время" : "Запустить время");
  });
  const speed = String(state.timeSpeed ?? 1);
  document.querySelectorAll("[data-time-speed]").forEach((sel) => {
    if (sel.value !== speed) sel.value = speed;
  });
  const atConj = state.timePauseReason === "conjunction";
  document.querySelectorAll("[data-time-pause-note]").forEach((el) => {
    el.hidden = !atConj;
  });
  syncTimeLoop(state);
}

function patchGlobe(state, prev) {
  if (globe) {
    if (!prev || state.layers !== prev.layers) globe.setLayers(state.layers);
    if (!prev || state.debrisVisible !== prev.debrisVisible) globe.setDebrisVisible(state.debrisVisible);
    if (!prev || state.globeTime !== prev.globeTime) globe.setTime(state.globeTime);
  }
  if (!prev || state.globeTime !== prev.globeTime) patchCharts(state);
  if (!prev || state.timePlaying !== prev.timePlaying || state.timeSpeed !== prev.timeSpeed || state.timePauseReason !== prev.timePauseReason) {
    patchTimeControls(state);
  }
}

function render(state, prev) {
  if (!uiChanged(state, prev)) {
    patchGlobe(state, prev);
    return;
  }
  const inner = state.view === "advanced" ? renderAdvancedView(state) : renderSimpleView(state);
  document.getElementById("app").innerHTML = shellHTML(state, inner);
  bindForm(document);
  bindActions(document);
  bindGlobeControls(document, state);
  mountGlobe(document, state);
  patchTimeControls(state);
}

async function boot() {
  if (!location.hash) location.hash = "#/simple";
  await enableMocks();
  store.subscribe(render);
  window.addEventListener("hashchange", syncViewFromHash);
  render(store.get(), null);
}

boot();
