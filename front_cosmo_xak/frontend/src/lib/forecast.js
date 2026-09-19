import { formatNumber } from "./format.js";

const S1_PFU = 10;
const R1_WM2 = 1e-5;

function toIsoZ(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function ageLabel(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const min = Math.max(0, Math.round((now - t) / 60_000));
  if (min < 60) return `${min} мин`;
  const hours = min / 60;
  if (hours < 10) return `${String(hours.toFixed(1)).replace(".", ",")} ч`;
  return `${Math.round(hours)} ч`;
}

function clamp01(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 0;
  return Math.min(1, Math.max(0, n));
}

function overlapMs(a0, a1, b0, b1) {
  return Math.min(a1, b1) - Math.max(a0, b0);
}

/** Form query → forecast API body. */
export function toForecastRequest(query) {
  const origin = toIsoZ(query?.start);
  const body = { origin };
  if (query?.historical_intent === "replay" && query?.cutoff_time) {
    body.cutoff = toIsoZ(query.cutoff_time);
  }
  return body;
}

export function windowSwRisk(window) {
  if (!window) return 0;
  const kp = Number(window.geomagnetic_kp_pred) || 0;
  const p10 = Number(window.proton_flux_above_10_mev_max_pred) || 0;
  const xray = Number(window.solar_xray_flux_long_max_pred) || 0;
  const s = Number(window.s_level) || 0;
  const g = Number(window.g_level) || 0;
  const r = Number(window.r_level) || 0;
  const scale = Math.max(s, g, r) / 5;
  const risk = Math.max(
    scale,
    kp / 9,
    Math.min(1, p10 / S1_PFU),
    Math.min(1, xray / R1_WM2),
    Number(window.p_adverse) || 0,
  );
  return clamp01(window.eva_allowed === false ? Math.max(risk, 0.65) : risk);
}

function normalizeWindow(raw) {
  if (!raw) return null;
  const start = toIsoZ(raw.start_utc || raw.start);
  const end = toIsoZ(raw.end_utc || raw.end);
  if (!start || !end) return null;
  const eva_allowed = raw.eva_allowed !== false;
  return {
    start,
    end,
    kp_interval_start: toIsoZ(raw.kp_interval_start_utc) || null,
    kp_interval_end: toIsoZ(raw.kp_interval_end_utc) || null,
    proton_flux_above_10_mev_mean_pred: raw.proton_flux_above_10_mev_mean_pred ?? null,
    proton_flux_above_10_mev_max_pred: raw.proton_flux_above_10_mev_max_pred ?? null,
    proton_flux_above_50_mev_max_pred: raw.proton_flux_above_50_mev_max_pred ?? null,
    proton_flux_above_100_mev_max_pred: raw.proton_flux_above_100_mev_max_pred ?? null,
    solar_xray_flux_long_max_pred: raw.solar_xray_flux_long_max_pred ?? null,
    geomagnetic_kp_pred: raw.geomagnetic_kp_pred ?? null,
    s_level: raw.s_level ?? 0,
    g_level: raw.g_level ?? 0,
    r_level: raw.r_level ?? 0,
    s_level_upper: raw.s_level_upper ?? null,
    g_level_upper: raw.g_level_upper ?? null,
    r_level_upper: raw.r_level_upper ?? null,
    p_adverse: raw.p_adverse ?? null,
    eva_allowed,
    blocked: !eva_allowed,
    status: eva_allowed ? "SAFE" : "BLOCKED",
    reason: Array.isArray(raw.reason) ? raw.reason.filter(Boolean) : [],
  };
}

export function mergeEvaAllowedBands(windows) {
  const sorted = [...(windows || [])]
    .filter((w) => w?.start && w?.end)
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const bands = [];
  for (const w of sorted) {
    const allowed = w.eva_allowed !== false;
    const last = bands[bands.length - 1];
    const touches = last && Date.parse(w.start) - Date.parse(last.end) <= 1000;
    if (last && last.eva_allowed === allowed && touches) {
      last.end = w.end;
      continue;
    }
    bands.push({
      start: w.start,
      end: w.end,
      eva_allowed: allowed,
      blocked: !allowed,
      status: allowed ? "SAFE" : "BLOCKED",
      peak_danger: allowed ? 0 : 1,
    });
  }
  return bands;
}

function clipWindows(windows, startIso, periodHours) {
  const t0 = Date.parse(startIso);
  const horizon = Number(periodHours);
  if (Number.isNaN(t0) || !Number.isFinite(horizon) || horizon <= 0) return windows || [];
  const t1 = t0 + horizon * 3600_000;
  return (windows || [])
    .map((w) => {
      const a = Date.parse(w.start);
      const b = Date.parse(w.end);
      if (Number.isNaN(a) || Number.isNaN(b) || b <= t0 || a >= t1) return null;
      return {
        ...w,
        start: toIsoZ(new Date(Math.max(a, t0)).toISOString()),
        end: toIsoZ(new Date(Math.min(b, t1)).toISOString()),
      };
    })
    .filter(Boolean);
}

export function seriesFromForecast(windows, startIso, periodHours) {
  const horizon = Number(periodHours) || 0;
  const t0 = Date.parse(startIso);
  if (Number.isNaN(t0) || horizon <= 0) return [];
  const pts = [];
  for (const w of windows || []) {
    const a = (Date.parse(w.start) - t0) / 3600_000;
    const b = (Date.parse(w.end) - t0) / 3600_000;
    if (!Number.isFinite(a) || !Number.isFinite(b) || b <= 0 || a >= horizon) continue;
    const risk = windowSwRisk(w);
    const left = Math.max(0, a);
    const right = Math.min(horizon, b);
    const point = {
      risk,
      eva_allowed: w.eva_allowed !== false,
      kp: w.geomagnetic_kp_pred,
      pfu10: w.proton_flux_above_10_mev_max_pred,
    };
    pts.push({ t: left, ...point });
    pts.push({ t: Math.max(left, right - 1e-6), ...point });
  }
  pts.sort((a, b) => a.t - b.t);
  return pts;
}

export function slotEvaAllowed(data, slot) {
  const windows = data?.forecast?.windows;
  if (!slot?.start || !slot?.end || !windows?.length) return null;
  const a0 = Date.parse(slot.start);
  const a1 = Date.parse(slot.end);
  if (Number.isNaN(a0) || Number.isNaN(a1)) return null;
  const overlapping = windows.filter((w) => {
    const b0 = Date.parse(w.start);
    const b1 = Date.parse(w.end);
    return Number.isFinite(b0) && Number.isFinite(b1) && overlapMs(a0, a1, b0, b1) > 0;
  });
  if (!overlapping.length) return null;
  return overlapping.every((w) => w.eva_allowed);
}

export function forecastOverlayWindows(data) {
  return data?.forecast?.overlay?.length ? data.forecast.overlay : [];
}

function patchForecastSource(metadata, payload, error) {
  const sources = [...(metadata?.sources || [])];
  const name = "CosmoHACK SW Forecast";
  const fetched = payload?.issued_at_utc || null;
  const row = {
    name,
    url: "http://92.255.110.133:8002/docs",
    fetched_at: fetched ? toIsoZ(fetched) : null,
    age: fetched ? ageLabel(fetched) : "—",
    status: error ? "error" : "ok",
    enabled: true,
    frozen: false,
  };
  const idx = sources.findIndex((s) => s.name === name);
  if (idx >= 0) sources[idx] = { ...sources[idx], ...row };
  else sources.push(row);
  return { ...metadata, sources };
}

function forecastWarnings(payload, windows, data) {
  const start = data.request?.start;
  const issued = toIsoZ(payload.issued_at_utc) || start;
  const policy = payload.policy || {};
  const peakKp = Math.max(0, ...windows.map((w) => Number(w.geomagnetic_kp_pred) || 0));
  const peakPfu = Math.max(0, ...windows.map((w) => Number(w.proton_flux_above_10_mev_max_pred) || 0));
  const peakXray = Math.max(0, ...windows.map((w) => Number(w.solar_xray_flux_long_max_pred) || 0));
  const peakP = Math.max(0, ...windows.map((w) => Number(w.p_adverse) || 0));
  const peakS = Math.max(0, ...windows.map((w) => Number(w.s_level) || 0));
  const peakG = Math.max(0, ...windows.map((w) => Number(w.g_level) || 0));
  const peakR = Math.max(0, ...windows.map((w) => Number(w.r_level) || 0));
  const blocked = windows.filter((w) => !w.eva_allowed);
  const horizon = {
    start: windows[0]?.start || start,
    end: windows[windows.length - 1]?.end || start,
  };
  const out = [
    {
      id: "w-sw-forecast-policy",
      type: "computed",
      mechanism: "radiation",
      title: blocked.length ? "Погода: выход запрещён на части горизонта" : "Погода: выход разрешён (S/G/R ниже порога)",
      source: "CosmoHACK SW Forecast",
      source_url: "http://92.255.110.133:8002/docs",
      published_at: issued,
      event_time: blocked[0]?.start || windows[0]?.start || start,
      period: blocked[0] ? { start: blocked[0].start, end: blocked[blocked.length - 1].end } : horizon,
      value: blocked.length ? 0 : 1,
      unit: "eva_allowed",
      impact: blocked.length
        ? `На ${blocked.length} получас. слотах eva_allowed = false (блок при S≥${policy.block_s ?? 1} / G≥${policy.block_g ?? 1} / R≥${policy.block_r ?? 1}).`
        : `Точечный прогноз S${peakS}/G${peakG}/R${peakR} ниже порога политики. Это не официальный допуск NOAA.`,
      rule: payload.decision_basis || "point_forecast_s_g_r_levels; eva_allowed",
      limitations: policy.threshold_rationale || payload.assessment_scope || "",
      confidence: "medium",
      intersects_window: true,
    },
    {
      id: "w-sw-forecast-kp",
      type: "forecast",
      mechanism: "radiation",
      title: `Kp прогноз ${formatNumber(peakKp, 1)}`,
      source: "CosmoHACK SW Forecast",
      source_url: payload.scale_sources?.noaa_scales || "https://www.spaceweather.gov/noaa-scales-explanation",
      published_at: issued,
      event_time: windows.find((w) => Number(w.geomagnetic_kp_pred) === peakKp)?.start || start,
      period: horizon,
      value: peakKp,
      unit: "Kp",
      impact: "Геомагнитный индекс на горизонте прогноза; G1 при Kp ≥ 5.",
      rule: policy.kp_to_g_method || "geomagnetic_kp_pred",
      limitations: "Kp задаётся на 3-часовых UTC-интервалах.",
      confidence: "medium",
      intersects_window: true,
    },
    {
      id: "w-sw-forecast-sep",
      type: "forecast",
      mechanism: "radiation",
      title: `SEP > 10 MeV ${formatNumber(peakPfu, 2)} pfu`,
      source: "CosmoHACK SW Forecast",
      source_url: payload.scale_sources?.noaa_scales || "https://www.spaceweather.gov/noaa-scales-explanation",
      published_at: issued,
      event_time: windows.find((w) => Number(w.proton_flux_above_10_mev_max_pred) === peakPfu)?.start || start,
      period: horizon,
      value: peakPfu,
      unit: "pfu",
      impact:
        peakPfu >= S1_PFU
          ? "Поток протонов на пороге или выше S1 (10 pfu)."
          : "Поток протонов ниже порога радиационного шторма S1 (10 pfu).",
      rule: "s_level from proton_flux_above_10_mev_max_pred; S1 = 10 pfu",
      limitations: "Исследовательский прогноз, не оперативный продукт NOAA SWPC.",
      confidence: "medium",
      intersects_window: true,
    },
    {
      id: "w-sw-forecast-xray",
      type: "forecast",
      mechanism: "radiation",
      title: `X-ray (long) ${peakXray.toExponential(1)} W/m²`,
      source: "CosmoHACK SW Forecast",
      source_url: payload.scale_sources?.noaa_scales || "https://www.spaceweather.gov/noaa-scales-explanation",
      published_at: issued,
      event_time: windows.find((w) => Number(w.solar_xray_flux_long_max_pred) === peakXray)?.start || start,
      period: horizon,
      value: peakXray,
      unit: "W/m2",
      impact: peakXray >= R1_WM2 ? "Поток на пороге или выше R1 (M1, 10⁻⁵ W/m²)." : "Поток ниже порога R1 (M1).",
      rule: "r_level from solar_xray_flux_long_max_pred; R1 = 1e-5 W/m²",
      limitations: "Длинноволновый канал; CME по этому API не оценивается.",
      confidence: "medium",
      intersects_window: true,
    },
    {
      id: "w-sw-forecast-padverse",
      type: "computed",
      mechanism: "radiation",
      title: `P(S/G/R ≥ 1) ${peakP.toExponential(1)}`,
      source: "CosmoHACK SW Forecast",
      source_url: "http://92.255.110.133:8002/docs",
      published_at: issued,
      event_time: windows.find((w) => Number(w.p_adverse) === peakP)?.start || start,
      period: horizon,
      value: peakP,
      unit: "p_adverse",
      impact: payload.probability_definition || "P(S≥1 or G≥1 or R≥1).",
      rule: "p_adverse",
      limitations: "Не зависит от порогов блока EVA в политике.",
      confidence: "medium",
      intersects_window: true,
    },
  ];
  return out;
}

function forecastErrorSw(prevSw, message) {
  return {
    ...prevSw,
    availability: "insufficient_data",
    risk_sw: null,
    time_series: [],
    warnings: [
      {
        id: "w-sw-forecast-error",
        type: "computed",
        mechanism: "radiation",
        title: "Прогноз космической погоды недоступен",
        source: "CosmoHACK SW Forecast",
        source_url: "http://92.255.110.133:8002/docs",
        published_at: new Date().toISOString(),
        event_time: null,
        period: null,
        value: null,
        unit: null,
        impact: message,
        rule: "POST /forecast",
        limitations: "Без прогноза SW не подставляется из мока.",
        confidence: "low",
        intersects_window: false,
      },
    ],
    components: { sep: null, cme: null, kp: null, flare: null },
    metrics: {
      kp: null,
      sep_10mev: null,
      sep_50mev: null,
      sep_100mev: null,
      cme_speed: null,
      xray_wm2: null,
      p_adverse: null,
    },
  };
}

/**
 * Attach live SW forecast. Does not replace orbit/EVA APIs.
 * eva_allowed bands become the chart overlay; SW series/metrics follow the forecast.
 */
export function applyForecast(data, payload, error = null) {
  if (!data) return data;
  if (error) {
    const message = error.message || "Forecast API недоступен.";
    return {
      ...data,
      metadata: patchForecastSource(data.metadata, null, error),
      risk_sw: forecastErrorSw(data.risk_sw, message),
      forecast: {
        available: false,
        error: message,
        windows: [],
        overlay: [],
      },
    };
  }
  if (!payload) {
    return {
      ...data,
      forecast: data.forecast || { available: false, windows: [], overlay: [], error: null },
    };
  }

  const startIso = data.request?.start;
  const period = Number(data.request?.period) || 0;
  const allWindows = (payload.windows || []).map(normalizeWindow).filter(Boolean);
  const windows = clipWindows(allWindows, startIso, period);
  const overlay = mergeEvaAllowedBands(windows);
  const series = seriesFromForecast(windows, startIso, period);
  const prevSw = data.risk_sw || {};
  const sourceWindows = allWindows.length ? allWindows : windows;
  const peakKp = Math.max(0, ...sourceWindows.map((w) => Number(w.geomagnetic_kp_pred) || 0));
  const peakPfu = Math.max(0, ...sourceWindows.map((w) => Number(w.proton_flux_above_10_mev_max_pred) || 0));
  const peakPfu50 = Math.max(0, ...sourceWindows.map((w) => Number(w.proton_flux_above_50_mev_max_pred) || 0));
  const peakPfu100 = Math.max(0, ...sourceWindows.map((w) => Number(w.proton_flux_above_100_mev_max_pred) || 0));
  const peakXray = Math.max(0, ...sourceWindows.map((w) => Number(w.solar_xray_flux_long_max_pred) || 0));
  const peakP = Math.max(0, ...sourceWindows.map((w) => Number(w.p_adverse) || 0));
  const peakS = Math.max(0, ...sourceWindows.map((w) => Number(w.s_level) || 0));
  const peakG = Math.max(0, ...sourceWindows.map((w) => Number(w.g_level) || 0));
  const peakR = Math.max(0, ...sourceWindows.map((w) => Number(w.r_level) || 0));
  const peakRisk = series.length ? Math.max(...series.map((p) => p.risk)) : null;
  const coversHorizon = Boolean(windows.length);
  const originIso = toIsoZ(payload.forecast_origin_utc) || (allWindows[0]?.start ?? null);
  const horizonEnd = allWindows.length ? allWindows[allWindows.length - 1].end : null;
  const coverageWarning = !coversHorizon && allWindows.length
    ? [{
        id: "w-sw-forecast-coverage",
        type: "computed",
        mechanism: "radiation",
        title: "Прогноз не покрывает выбранный горизонт ВКД",
        source: "CosmoHACK SW Forecast",
        source_url: "http://92.255.110.133:8002/docs",
        published_at: toIsoZ(payload.issued_at_utc) || originIso,
        event_time: originIso,
        period: originIso && horizonEnd ? { start: originIso, end: horizonEnd } : null,
        value: null,
        unit: null,
        impact: `API вернул origin ${originIso || "—"}${horizonEnd ? ` … ${horizonEnd}` : ""}; слоты ВКД с ${startIso} в этот интервал не попадают.`,
        rule: "POST /forecast; clip to request.period",
        limitations: "Пики Kp/SEP/X-ray ниже — по горизонту модели, не по выбранному окну.",
        confidence: "medium",
        intersects_window: false,
      }]
    : [];
  const swWarnings = coversHorizon
    ? forecastWarnings(payload, sourceWindows, data)
    : coverageWarning;

  return {
    ...data,
    metadata: {
      ...patchForecastSource(data.metadata, payload, null),
      confidence_sw: coversHorizon ? "средняя" : "низкая",
    },
    risk_sw: {
      ...prevSw,
      availability: coversHorizon ? "ok" : "insufficient_data",
      risk_sw: peakRisk,
      components: {
        sep: clamp01(peakPfu / S1_PFU),
        cme: null,
        kp: clamp01(peakKp / 9),
        flare: clamp01(peakXray / R1_WM2),
      },
      metrics: {
        kp: peakKp,
        sep_10mev: peakPfu,
        sep_50mev: peakPfu50,
        sep_100mev: peakPfu100,
        cme_speed: null,
        xray_wm2: peakXray,
        p_adverse: peakP,
      },
      time_series: series,
      warnings: swWarnings,
    },
    forecast: {
      available: true,
      error: null,
      covers_horizon: coversHorizon,
      model_type: payload.model_type || null,
      model_version: payload.model_version || null,
      policy_version: payload.policy_version || payload.policy?.version || null,
      forecast_origin_utc: originIso,
      issued_at_utc: toIsoZ(payload.issued_at_utc),
      eva_allowed_all: coversHorizon ? windows.every((w) => w.eva_allowed) : null,
      peak: {
        kp: peakKp,
        sep_10mev: peakPfu,
        sep_50mev: peakPfu50,
        sep_100mev: peakPfu100,
        xray_wm2: peakXray,
        p_adverse: peakP,
        s: peakS,
        g: peakG,
        r: peakR,
      },
      windows: allWindows,
      overlay,
    },
  };
}
