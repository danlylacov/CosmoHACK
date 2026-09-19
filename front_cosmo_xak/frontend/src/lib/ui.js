import { escapeHtml, formatUtc, formatNumber, scrubberLabel } from "../lib/format.js";
import { debrisTrackId, debrisMissKm } from "./orbits.js";

export function loadingHTML(text = "Загрузка…") {
  return `<div class="load-state"><div class="spinner" role="status" aria-label="Загрузка"></div><p>${escapeHtml(text)}</p></div>`;
}

export function errorHTML(message) {
  return `
    <div class="load-state">
      <p>${escapeHtml(message || "Не удалось выполнить запрос.")}</p>
      <button class="btn btn--primary" type="button" data-action="retry">Повторить</button>
    </div>
  `;
}

export function emptyHTML(message = "Нет данных для отображения") {
  return `<div class="load-state"><p>${escapeHtml(message)}</p></div>`;
}

export function idleStageHTML(state = {}) {
  const loading = state.loadStatus === "loading";
  const error = state.loadStatus === "error" ? state.errorMessage : null;
  return `
    <figure class="idle-stage">
      <img
        src="/idle-eva.png?v=2"
        alt="Космонавт в невесомости среди планет"
        width="980"
        height="980"
      >
      ${loading ? `
        <div class="idle-stage__veil" role="status">
          <div class="spinner" aria-label="Загрузка"></div>
          <p>Расчёт сценария ВКД…</p>
        </div>
      ` : ""}
      ${error && !loading ? `
        <div class="idle-stage__veil">
          ${errorHTML(error)}
        </div>
      ` : ""}
    </figure>
  `;
}

export function workspaceChromeHTML(view) {
  return `
    <div class="query-mast">
      <nav class="view-switch" aria-label="Режим интерфейса">
        <a href="#/simple" class="${view === "simple" ? "is-active" : ""}">Коротко</a>
        <a href="#/advanced" class="${view === "advanced" ? "is-active" : ""}">Детально</a>
      </nav>
      <span class="tz-chip" title="Все метки времени в интерфейсе — UTC">UTC</span>
      <h1>Параметры ВКД</h1>
    </div>
  `;
}

export function shellHTML(_state, inner) {
  return `
    <div class="app-shell">
      <div class="brand-rail" aria-hidden="true">
        <span class="brand-rail__text">ВКД · МКС</span>
        <span class="brand-rail__text">Космохакатон 2026</span>
      </div>
      <main class="main" id="main">${inner}</main>
    </div>
  `;
}

export function globeChromeHTML(data, { simple, layers, debrisVisible = {} }) {
  const debris = data?.orbit?.debris || [];
  const objectsHtml = debris.length
    ? `
      <fieldset class="globe-objects">
        <legend>Объекты</legend>
        <ul>
          ${debris.map((track, i) => {
            const id = debrisTrackId(track, i);
            const miss = debrisMissKm(track);
            const checked = debrisVisible[id] !== false ? "checked" : "";
            const label = track.name || (track.norad_id != null ? `NORAD ${track.norad_id}` : "объект");
            const dist = miss != null ? `<span>${escapeHtml(formatNumber(miss, 0))} км</span>` : "";
            return `<li><label><input type="checkbox" data-debris-id="${escapeHtml(id)}" ${checked}> ${escapeHtml(label)} ${dist}</label></li>`;
          }).join("")}
        </ul>
      </fieldset>
    `
    : "<p>MMOD не прогнозируется</p>";

  const layersHtml = simple
    ? ""
    : `
      <div class="globe-layers">
        <label><input type="checkbox" data-layer="mmod" ${layers.mmod ? "checked" : ""}> MMOD</label>
        <label><input type="checkbox" data-layer="radiation" ${layers.radiation ? "checked" : ""}> Радиация</label>
        <label><input type="checkbox" data-layer="illumination" ${layers.illumination ? "checked" : ""}> Освещённость</label>
      </div>
    `;

  return `
    <div class="globe-wrap ${simple ? "globe-wrap--simple" : "globe-wrap--advanced"}">
      <div class="globe-stage" data-globe></div>
      <div class="globe-info">${objectsHtml}</div>
      ${layersHtml}
    </div>
  `;
}

export const TIME_SPEEDS = [0.25, 0.5, 1, 2, 4, 8];

function timePlayButton(playing) {
  const label = playing ? "Остановить время" : "Запустить время";
  return `
    <button type="button" class="time-play" data-time-play aria-pressed="${playing ? "true" : "false"}" aria-label="${label}">
      <svg class="time-play__play" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
        <path fill="currentColor" d="M3 1.5v9l8-4.5z"/>
      </svg>
      <svg class="time-play__pause" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
        <path fill="currentColor" d="M2.5 1.5h2.5v9H2.5zm4.5 0h2.5v9H7z"/>
      </svg>
    </button>
  `;
}

function timeSpeedSelect(speed) {
  const current = TIME_SPEEDS.includes(Number(speed)) ? Number(speed) : 1;
  const options = TIME_SPEEDS.map((v) => {
    const selected = v === current ? " selected" : "";
    return `<option value="${v}"${selected}>${v}×</option>`;
  }).join("");
  return `
    <label class="time-speed">
      <span class="sr-only">Скорость времени</span>
      <select data-time-speed aria-label="Скорость времени">${options}</select>
    </label>
  `;
}

export function timeScrubberHTML(data, globeTime, maxHours, { id = "scrubber", variant = "", playing = false, speed = 1 } = {}) {
  const startIso = data?.request?.start;
  const scrubLabel = startIso ? scrubberLabel(startIso, globeTime) : `${Number(globeTime).toFixed(2)} ч`;
  const klass = variant ? `scrubber scrubber--${variant}` : "scrubber";
  return `
    <div class="${klass}">
      <div class="scrubber__head">
        ${timePlayButton(playing)}
        ${timeSpeedSelect(speed)}
        <label class="panel__kicker" for="${id}">Время · <span class="mono" data-scrubber-label>${escapeHtml(scrubLabel)}</span></label>
        <p class="scrubber__pause-note" data-time-pause-note hidden>Пауза из-за сближения</p>
      </div>
      <div class="scrubber__rail">
        <input id="${id}" data-scrubber type="range" min="0" max="${maxHours}" step="0.05" value="${globeTime}" aria-valuetext="${escapeHtml(scrubLabel)}">
      </div>
    </div>
  `;
}

export { escapeHtml, formatUtc };
