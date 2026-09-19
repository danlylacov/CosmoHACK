import { addHoursIso, formatNumber, formatUtc, hoursFromStart } from "./format.js";
import { slotEvaAllowed } from "./forecast.js";

const MOCK_SOURCE = /NOAA SWPC|DONKI|Space-Track CDM|IAU Meteor/i;
const MOCK_WARNING = /donki|enlil|ариет|iau meteor|space-track cdm|wsa-enlil/i;

function round(n, d = 3) {
  return Number(Number(n).toFixed(d));
}

function clamp01(x) {
  const n = Number(x);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

export function isMockCatalogSource(source) {
  return MOCK_SOURCE.test(`${source?.name || ""} ${source?.url || ""}`);
}

export function isMockCatalogWarning(warning) {
  if (!warning) return false;
  const id = String(warning.id || "");
  if (/^w-sw-cme|^w-sw-kp$|^w-sw-sep$|^w-mmod-meteor|^w-mmod-conj|^verify-/.test(id)) return true;
  return MOCK_WARNING.test(`${id} ${warning.title || ""} ${warning.source || ""}`);
}

function seriesStats(series, t0, t1) {
  let maxRisk = 0;
  let overlap = 0;
  const rows = series || [];
  for (let i = 0; i < rows.length; i += 1) {
    const p = rows[i];
    if (p.t < t0 - 1e-9 || p.t > t1 + 1e-9) continue;
    maxRisk = Math.max(maxRisk, p.risk || 0);
    const next = rows[i + 1];
    const dt = next && next.t <= t1 + 1e-9 ? Math.max(0, next.t - p.t) : 0;
    if ((p.risk || 0) >= 0.3 || p.eva_allowed === false) overlap += dt;
  }
  return { maxRisk: round(maxRisk, 3), overlap: round(overlap, 2) };
}

export function evaWindowToRow(window) {
  if (!window) return null;
  const danger = Number(window.peak_danger ?? window.danger);
  const coverage = window.data_coverage;
  return {
    start: window.start,
    end: window.end,
    score: window.safety != null ? window.safety : Number.isFinite(danger) ? round(clamp01(1 - danger), 3) : 0,
    risk_sw: 0,
    risk_mmod: Number.isFinite(danger) ? danger : 0,
    overlap_sw: 0,
    overlap_mmod: (Number(window.critical_overlap_seconds) || 0) / 3600,
    completeness: coverage != null ? coverage : 1,
    requires_check: Boolean(window.blocked) || (coverage != null && coverage < 0.9),
    distance_min_km: window.distance_min_km ?? null,
    blocked: Boolean(window.blocked),
    status: window.status || null,
    eva_allowed: null,
  };
}

export function candidateWindows(query) {
  const start = query?.start;
  const duration = Number(query?.duration);
  const period = Number(query?.period);
  if (!start || !Number.isFinite(duration) || !Number.isFinite(period) || duration <= 0 || period < duration) {
    return [];
  }
  const step = duration >= 2 ? 1 : Math.max(0.5, duration);
  const out = [];
  for (let t = 0; t + duration <= period + 1e-9; t += step) {
    out.push({
      start: addHoursIso(start, t),
      end: addHoursIso(start, t + duration),
      score: 0,
      risk_sw: 0,
      risk_mmod: 0,
      overlap_sw: 0,
      overlap_mmod: 0,
      completeness: 1,
      requires_check: false,
      eva_allowed: null,
    });
  }
  return out;
}

export function stripMockCatalog(data) {
  if (!data) return data;
  const sources = (data.metadata?.sources || []).filter((s) => !isMockCatalogSource(s));
  const ok = sources.filter((s) => s.status === "ok" || s.status === "COMPLETE" || s.enabled !== false).length;
  const completeness = sources.length ? round(ok / sources.length, 2) : data.metadata?.completeness;
  return {
    ...data,
    risk_sw: {
      ...data.risk_sw,
      warnings: (data.risk_sw?.warnings || []).filter((w) => !isMockCatalogWarning(w)),
      components: {
        ...(data.risk_sw?.components || {}),
        cme: null,
      },
      metrics: {
        ...(data.risk_sw?.metrics || {}),
        cme_speed: null,
      },
    },
    risk_mmod: {
      ...data.risk_mmod,
      warnings: (data.risk_mmod?.warnings || []).filter((w) => !isMockCatalogWarning(w)),
      components: {
        ...(data.risk_mmod?.components || {}),
        meteor: null,
      },
      metrics: {
        ...(data.risk_mmod?.metrics || {}),
        pc: null,
      },
    },
    verification: data.verification
      ? {
          ...data.verification,
          later_observations: (data.verification.later_observations || []).filter((w) => !isMockCatalogWarning(w)),
        }
      : data.verification,
    metadata: {
      ...data.metadata,
      sources,
      completeness,
    },
  };
}

function isoInWindow(iso, startIso, endIso) {
  const t = Date.parse(iso);
  const a = Date.parse(startIso);
  const b = Date.parse(endIso);
  return Number.isFinite(t) && Number.isFinite(a) && Number.isFinite(b) && t >= a && t <= b;
}

function intervalOverlapsWindow(iv, w) {
  const tcaHit = isoInWindow(iv?.tca, w.start, w.end);
  const s = iv?.start_time || iv?.start;
  const e = iv?.end_time || iv?.end || iv?.tca || s;
  if (!s || !e) return tcaHit;
  return Date.parse(s) <= Date.parse(w.end) && Date.parse(e) >= Date.parse(w.start);
}

function windowHasConjunction(w, data) {
  const tca = data?.orbit?.conjunction?.summary?.tca ?? data?.risk_mmod?.metrics?.tca;
  if (isoInWindow(tca, w.start, w.end)) return true;
  return (data?.orbit?.conjunction?.critical_intervals || []).some((iv) => intervalOverlapsWindow(iv, w));
}

export function rescoreWindowsLive(data) {
  if (!data) return data;
  const startIso = data.request?.start;
  const fromEva = Boolean(data.eva?.available && data.eva.windows?.length);
  const seed = fromEva
    ? data.eva.windows.map(evaWindowToRow).filter(Boolean)
    : candidateWindows(data.request);
  if (!startIso || !seed.length) return { ...data, windows: seed };
  const sw = data.risk_sw?.time_series || [];
  const mm = data.risk_mmod?.time_series || [];
  const windows = seed.map((w) => {
    const t0 = hoursFromStart(w.start, startIso);
    const t1 = hoursFromStart(w.end, startIso);
    const swS = seriesStats(sw, t0, t1);
    const mmS = seriesStats(mm, t0, t1);
    const allowed = slotEvaAllowed(data, w);
    const risk_mmod = fromEva ? w.risk_mmod : mmS.maxRisk;
    const contains_conjunction = windowHasConjunction(w, data);
    const score = fromEva
      ? w.score
      : round(clamp01(1 - 0.55 * swS.maxRisk - 0.45 * risk_mmod - 0.04 * swS.overlap - 0.03 * mmS.overlap), 3);
    return {
      ...w,
      risk_sw: swS.maxRisk,
      risk_mmod,
      overlap_sw: swS.overlap,
      overlap_mmod: fromEva ? w.overlap_mmod : mmS.overlap,
      eva_allowed: allowed,
      contains_conjunction,
      requires_check:
        Boolean(w.requires_check) ||
        allowed === false ||
        contains_conjunction ||
        swS.maxRisk > 0.35 ||
        risk_mmod > 0.35,
      score,
    };
  });
  if (!fromEva) {
    windows.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }
  const winner = windows[0] || null;
  return {
    ...data,
    windows,
    recommendation: {
      ...data.recommendation,
      window: winner ? { start: winner.start, end: winner.end } : data.recommendation?.window,
      score: winner?.score ?? data.recommendation?.score,
    },
  };
}

export function refreshLiveReason(data) {
  if (!data) return data;
  const parts = [];
  if (data.forecast?.available) {
    if (data.forecast.covers_horizon === false) {
      parts.push("Прогноз есть, но не покрывает выбранный горизонт ВКД");
    } else {
      parts.push(
        data.forecast.eva_allowed_all
          ? "Погода: выход разрешён (S/G/R ниже порога политики)"
          : "Погода: выход запрещён на части горизонта",
      );
    }
  } else if (data.forecast?.error) {
    parts.push("Прогноз космической погоды недоступен");
  }
  const miss = data.risk_mmod?.metrics?.miss_distance;
  const obj = data.orbit?.conjunction?.summary?.nearest_object;
  const name = obj?.name || (obj?.norad_id != null ? `NORAD ${obj.norad_id}` : "объект");
  if (miss != null && Number.isFinite(Number(miss))) {
    const tca = data.risk_mmod?.metrics?.tca;
    parts.push(`ближайший объект ${name}: miss ${formatNumber(miss, 2)} км${tca ? `, TCA ${formatUtc(tca)}` : ""}`);
  } else if (data.risk_mmod?.availability === "insufficient_data") {
    parts.push("MMOD не прогнозируется");
  }
  const best = data.eva?.windows?.[0];
  if (data.eva?.available && best) {
    parts.push("ранжирование слотов — EVA API");
  }
  const limitations = [
    data.forecast?.available ? "SW — CosmoHACK Forecast (eva_allowed, S/G/R, Kp, SEP, X-ray)" : null,
    "MMOD — CosmoHACK Orbit API (miss/TCA); Pc и CDM нет",
    data.eva?.available ? "слоты — EVA API" : null,
    "CME и метеорные потоки в этих API нет",
  ]
    .filter(Boolean)
    .join(". ");
  return {
    ...data,
    recommendation: {
      ...data.recommendation,
      reason: parts.join(". ") || data.recommendation?.reason,
      limitations,
    },
  };
}

export function patchLiveHealth(data, health) {
  if (!data?.metadata?.sources || !health) return data;
  const match = (source, probe) => {
    const blob = `${source?.name || ""} ${source?.url || ""}`;
    if (probe === "orbit") return /celestrak|socrates|orbit api/i.test(blob);
    if (probe === "eva") return /eva api/i.test(blob);
    if (probe === "forecast") return /sw forecast|forecast/i.test(blob);
    return false;
  };
  const sources = data.metadata.sources.map((s) => {
    const hit = ["orbit", "eva", "forecast"].map((key) => (match(s, key) ? health[key] : null)).find(Boolean);
    if (!hit) return s;
    return {
      ...s,
      status: hit.ok ? "ok" : "error",
    };
  });
  return { ...data, metadata: { ...data.metadata, sources } };
}

export function finalizeLive(data) {
  return refreshLiveReason(rescoreWindowsLive(stripMockCatalog(data)));
}
