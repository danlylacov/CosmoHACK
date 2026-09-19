import { queryPanelHTML } from "./QueryForm.js";
import { windowComparisonHTML } from "./WindowComparison.js";
import { warningsListHTML } from "./WarningCard.js";
import { sourceStatusHTML } from "./SourceStatus.js";
import { exportPanelHTML } from "./ExportPanel.js";
import { globeChromeHTML, idleStageHTML, timeScrubberHTML, escapeHtml } from "../lib/ui.js";
import { formatUtc, formatNumber, formatSci, hoursFromStart, formatUtcShort, addHoursIso } from "../lib/format.js";
import { formatChartNow, chartDotXY } from "../lib/orbits.js";
import { collectWarnings, riskTone, dataReliable, availabilityNote } from "../lib/risk.js";
import { describePlanChange } from "../lib/plan.js";
import { evaWindowTone, overlayWindows } from "../lib/eva.js";

function bandFill(tone) {
  if (tone === "ok") return "var(--ok)";
  if (tone === "warn") return "var(--warn)";
  if (tone === "danger") return "var(--danger)";
  return "var(--red)";
}

function windowBandsSVG(windows, maxT, width, padT, innerH) {
  const xAt = (t) => (t / maxT) * width;
  return (windows || [])
    .map((win, i) => {
      const t0 = hoursFromStart(win.start, win._startIso);
      const t1 = hoursFromStart(win.end, win._startIso);
      if (!Number.isFinite(t0) || !Number.isFinite(t1)) return "";
      const wx = xAt(Math.max(0, Math.min(maxT, t0)));
      const we = xAt(Math.max(0, Math.min(maxT, t1)));
      const ww = Math.max(4, we - wx);
      const tone = evaWindowTone(win);
      const fill = bandFill(tone);
      const top = i === 0;
      return `<rect x="${wx}" y="${padT}" width="${ww}" height="${innerH}" fill="${fill}" fill-opacity="${top ? 0.22 : 0.1}" stroke="${fill}" stroke-opacity="${top ? 0.9 : 0.35}" stroke-width="${top ? 2.4 : 1}"></rect>`;
    })
    .join("");
}

function chartSVG(series, color, windows, maxT, warnings, startIso, globeTime) {
  const w = 1000;
  const h = 220;
  const padT = 14;
  const padB = 14;
  const innerH = h - padT - padB;
  const xAt = (t) => (t / maxT) * w;
  const yAt = (r) => padT + (1 - r) * innerH;
  const pts = series.length
    ? series
    : [
        { t: 0, risk: 0 },
        { t: maxT, risk: 0 },
      ];
  const line = pts.map((p, i) => `${i ? "L" : "M"}${xAt(p.t)},${yAt(p.risk)}`).join(" ");
  const area = `${line} L${xAt(pts[pts.length - 1].t)},${yAt(0)} L${xAt(pts[0].t)},${yAt(0)} Z`;
  const stamped = (windows || []).map((win) => ({ ...win, _startIso: startIso }));
  const grid = [0, 0.5, 1]
    .map((r) => `<line x1="0" x2="${w}" y1="${yAt(r)}" y2="${yAt(r)}" stroke="var(--wash)" stroke-width="1.2" />`)
    .join("");
  const marks = warnings
    .map((warn) => {
      const tIso = warn.event_time || warn.period?.start;
      if (!tIso) return "";
      const t = hoursFromStart(tIso, startIso);
      const x = xAt(t);
      return `<line x1="${x}" x2="${x}" y1="${padT}" y2="${h - padB}" stroke="var(--red)" stroke-width="2" stroke-dasharray="5 4" />`;
    })
    .join("");
  const dot = chartDotXY(series, globeTime, maxT, w, h);
  return `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      ${grid}
      ${windowBandsSVG(stamped, maxT, w, padT, innerH)}
      <path d="${area}" fill="${color}" fill-opacity="0.22"></path>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"></path>
      ${marks}
      <circle data-chart-dot cx="${dot.x}" cy="${dot.y}" r="9" fill="${color}" stroke="#fff" stroke-width="3"></circle>
    </svg>
  `;
}

function metricValue(value, availability) {
  const tone = riskTone(value, availability);
  if (availability && availability !== "ok") return `<p class="metric-card__value is-unknown">н/д</p>`;
  return `<p class="metric-card__value is-${tone}">${formatNumber(value, 2)}</p>`;
}

function timeAxisHTML(maxT, startIso) {
  const step = maxT <= 8 ? 1 : maxT <= 12 ? 2 : maxT <= 18 ? 3 : 4;
  const ticks = [];
  for (let t = 0; t <= maxT + 0.001; t += step) {
    ticks.push(`<span class="chart-axis__tick"><span class="chart-axis__hm">${escapeHtml(formatUtcShort(addHoursIso(startIso, t)).replace(" UTC", ""))}</span><span class="chart-axis__utc"> UTC</span><small>+${Number(t.toFixed(0))} ч</small></span>`);
  }
  return `<div class="chart-axis" aria-hidden="true"><div class="chart-axis__ticks">${ticks.join("")}</div></div>`;
}

function chartRowHTML(title, key, color, series, windows, maxT, warnings, startIso, globeTime) {
  const now = formatChartNow(key, series, globeTime);
  return `
    <article class="chart chart--${key}">
      <header class="chart__head">
        <h3 class="chart__name">${title}</h3>
        <p class="chart__now">сейчас <b data-chart-now="${key}">${now}</b></p>
      </header>
      <div class="chart__scale" aria-hidden="true"><span>1</span><span>0,5</span><span>0</span></div>
      <div class="chart__plot" data-chart-scrub>
        ${chartSVG(series, color, windows, maxT, warnings, startIso, globeTime)}
      </div>
    </article>
  `;
}

function tonedNum(value, digits = 2) {
  return `<b class="metric-tone is-${riskTone(value)}">${formatNumber(value, digits)}</b>`;
}

function metricsHTML(data) {
  const swAv = data.risk_sw.availability || "ok";
  const mmAv = data.risk_mmod.availability || "ok";
  const { kp, sep_10mev, sep_50mev, sep_100mev, cme_speed, xray_wm2, p_adverse } = data.risk_sw.metrics || {};
  const { miss_distance, pc, tca, relative_speed_km_s } = data.risk_mmod.metrics || {};
  const c = data.risk_sw.components || {};
  const m = data.risk_mmod.components || {};
  const swNote = availabilityNote(swAv);
  const mmNote = availabilityNote(mmAv);
  const fc = data.forecast;
  const swPermit = fc?.available && fc.eva_allowed_all != null
    ? fc.eva_allowed_all
      ? `<dt>Выход (SW)</dt><dd>разрешён</dd>`
      : `<dt>Выход (SW)</dt><dd>запрещён на части горизонта</dd>`
    : "";
  const sgr = fc?.peak
    ? `<dt>S / G / R</dt><dd>S${fc.peak.s} G${fc.peak.g} R${fc.peak.r}</dd>`
    : "";

  return `
    <div class="metrics">
      <article class="metric-card metric-card--sw">
        <p class="panel__kicker">Механизм 1 · космическая погода</p>
        <h3>Risk SW</h3>
        ${metricValue(data.risk_sw.risk_sw, swAv)}
        ${swNote ? `<p class="muted">${escapeHtml(swNote)}</p>` : ""}
        <div class="breakdown">
          <span>sep ${tonedNum(c.sep)}</span>
          <span>cme ${tonedNum(c.cme)}</span>
          <span>kp ${tonedNum(c.kp)}</span>
          <span>flare ${tonedNum(c.flare)}</span>
        </div>
        <dl class="kv">
          <dt>Kp</dt><dd>${formatNumber(kp, 1)}</dd>
          <dt>SEP &gt; 10 MeV</dt><dd>${formatNumber(sep_10mev, 1)} pfu</dd>
          <dt>SEP &gt; 50 MeV</dt><dd>${formatNumber(sep_50mev, 1)} pfu</dd>
          <dt>SEP &gt; 100 MeV</dt><dd>${formatNumber(sep_100mev, 1)} pfu</dd>
          <dt>X-ray</dt><dd>${formatSci(xray_wm2)} W/m²</dd>
          <dt>P(adverse)</dt><dd>${formatSci(p_adverse)}</dd>
          <dt>CME speed</dt><dd>${formatNumber(cme_speed, 0)} km/s</dd>
          ${sgr}
          ${swPermit}
        </dl>
      </article>
      <article class="metric-card metric-card--mmod">
        <p class="panel__kicker">Механизм 2 · MMOD</p>
        <h3>Risk MMOD</h3>
        ${metricValue(data.risk_mmod.risk_mmod, mmAv)}
        ${mmNote ? `<p class="muted">${escapeHtml(mmNote)}</p>` : ""}
        <div class="breakdown">
          <span>conjunction ${tonedNum(m.conjunction)}</span>
          <span>meteor ${tonedNum(m.meteor)}</span>
        </div>
        <dl class="kv">
          <dt>Miss distance</dt><dd>${formatNumber(miss_distance, 1)} км</dd>
          <dt>Относит. скорость</dt><dd>${formatNumber(relative_speed_km_s, 1)} км/с</dd>
          <dt>Pc</dt><dd>${formatSci(pc)}</dd>
          <dt>TCA</dt><dd>${formatUtc(tca)}</dd>
        </dl>
      </article>
    </div>
  `;
}

function chartMaxHours(data) {
  return Math.max(1, Number(data.request?.period) || 0);
}

function timelineHTML(data, globeTime, clock = {}) {
  const maxT = chartMaxHours(data);
  const windows = overlayWindows(data);
  const startIso = data.request.start;
  const fromForecast = Boolean(data.forecast?.overlay?.length);
  const fromEva = Boolean(data.eva?.windows?.length);
  const overlayNote = fromForecast
    ? "Цветные полосы — разрешение выхода по прогнозу космической погоды (eva_allowed): зелёный — разрешён, красный — запрещён."
    : fromEva
      ? "Цветные полосы — предложенные окна EVA API (ярче — лучший слот)."
      : "Светлая полоса — рекомендованное окно.";

  return `
    <div class="timeline" data-chart-max="${maxT}">
      ${timeScrubberHTML(data, globeTime, maxT, { id: "scrubber-charts", playing: clock.playing, speed: clock.speed })}
      ${chartRowHTML("Радиация", "sw", "var(--petrol)", data.risk_sw.time_series, windows, maxT, data.risk_sw.warnings || [], startIso, globeTime)}
      ${chartRowHTML("MMOD", "mmod", "var(--red)", data.risk_mmod.time_series, windows, maxT, data.risk_mmod.warnings || [], startIso, globeTime)}
      ${timeAxisHTML(maxT, startIso)}
      <p class="muted">Заливка кривой — риск во времени; у MMOD — расстояние до ближайшего объекта по сближениям (сейчас — км). ${overlayNote} Штрихи — предупреждения. Точка на кривой — выбранный момент; его можно сдвинуть, перетаскивая график.</p>
    </div>
  `;
}

function verificationHTML(data) {
  const v = data.verification;
  const later = v?.later_observations || [];
  if (!later.length) return "";
  return `
    <div class="stack">
      <p class="muted">
        Стало известно уже после отсечки и в оценку не входит.
        Нужно, чтобы сравнить прогноз с тем, что произошло на самом деле.
      </p>
      <dl class="kv warning-item__meta">
        <dt>Отсечка UTC</dt>
        <dd>${escapeHtml(formatUtc(v.cutoff_time))}</dd>
        <dt>В расчёте</dt>
        <dd>не использовались</dd>
        <dt>Смысл</dt>
        <dd>${escapeHtml(v.note || "Поздние уточнения показаны отдельно.")}</dd>
      </dl>
      ${warningsListHTML(later, data.metadata.sources, true)}
    </div>
  `;
}

function resultBlock(state) {
  const data = state.data;
  const reliable = dataReliable(data);

  return `
    ${!reliable ? `<p class="muted">Данных не хватает: нельзя читать результат как «всё безопасно».</p>` : ""}
    ${globeChromeHTML(data, {
      simple: false,
      layers: state.layers,
      debrisVisible: state.debrisVisible,
    })}
  `;
}

export function renderAdvancedView(state) {
  const data = state.data;
  if (!data) {
    return `
      <div class="workspace-layout is-idle">
        ${queryPanelHTML(state)}
        ${idleStageHTML(state)}
      </div>
    `;
  }

  const planNote = describePlanChange(state.previousQuery, state.query, state.previousData, data);
  return `
    <div class="workspace-layout">
      ${queryPanelHTML(state)}

      <section class="panel">
        <div class="panel__head">
          <div>
            <p class="panel__kicker">3D-сцена</p>
            <h2>Орбита МКС</h2>
          </div>
        </div>
        ${timeScrubberHTML(data, state.globeTime, chartMaxHours(data), {
          id: "scrubber-orbit",
          variant: "orbit",
          playing: state.timePlaying,
          speed: state.timeSpeed,
        })}
        ${resultBlock(state)}
      </section>

      <section class="panel span-all charts-col">
        <div class="panel__head">
          <div>
            <p class="panel__kicker">Динамика</p>
            <h2>Радиация и MMOD</h2>
          </div>
        </div>
        ${timelineHTML(data, state.globeTime, { playing: state.timePlaying, speed: state.timeSpeed })}
      </section>

      <aside class="panel metrics-col">
        <div class="panel__head">
          <div>
            <p class="panel__kicker">Метрики</p>
            <h2>Два механизма риска</h2>
          </div>
        </div>
        ${metricsHTML(data)}
      </aside>

      <section class="panel span-all">
        <div class="panel__head">
          <div>
            <p class="panel__kicker">Сравнение</p>
            <h2>Окна одинаковой длительности</h2>
          </div>
        </div>
        ${windowComparisonHTML(data, planNote)}
      </section>

      ${data.verification?.later_observations?.length ? `
        <section class="panel span-all">
          <div class="panel__head">
            <div>
              <p class="panel__kicker">Сверка с фактом</p>
              <h2>Что узнали позже</h2>
            </div>
          </div>
          ${verificationHTML(data)}
        </section>
      ` : ""}

      <section class="panel span-all">
        <div class="panel__head">
          <div>
            <p class="panel__kicker">Объяснения</p>
            <h2>Предупреждения в расчёте</h2>
          </div>
        </div>
        ${warningsListHTML(collectWarnings(data), data.metadata.sources)}
      </section>

      <section class="panel span-all">
        <div class="panel__head">
          <div>
            <p class="panel__kicker">Источники</p>
            <h2>Статус данных</h2>
          </div>
        </div>
        ${sourceStatusHTML(data.metadata, { sourceControls: state.sourceControls })}
      </section>

      <section class="panel span-all">
        <div class="panel__head">
          <div>
            <p class="panel__kicker">Экспорт</p>
            <h2>Выгрузка расчёта</h2>
          </div>
        </div>
        ${exportPanelHTML()}
      </section>
    </div>
  `;
}
