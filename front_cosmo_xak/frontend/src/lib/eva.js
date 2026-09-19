import { EVA_CRITICAL_MAX_KM, EVA_STEP_MIN, EVA_TOP_K } from "../config.js";
import { toOrbitWindow } from "./orbits.js";
import { forecastOverlayWindows } from "./forecast.js";
import { evaWindowToRow } from "./live.js";

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

function qualityStatus(dq) {
  const s = String(dq || "");
  if (/STALE/i.test(s)) return "stale";
  if (/ERROR|FAIL/i.test(s)) return "error";
  if (/COMPLETE|OK/i.test(s)) return "ok";
  return s ? "ok" : "error";
}

/** Form query → EVA API body. Duration is hours on the form, minutes on the wire. */
export function toEvaWindowsRequest(query) {
  const { start_time, end_time } = toOrbitWindow(query);
  const durationHours = Number(query?.duration);
  const duration_min = Math.max(1, Math.round((Number.isFinite(durationHours) ? durationHours : 1) * 60));
  const raw = Number(query?.critical_distance_km);
  const finite = Number.isFinite(raw) ? raw : EVA_CRITICAL_MAX_KM;
  const critical_distance_km = Math.min(EVA_CRITICAL_MAX_KM, Math.max(0.01, finite));
  return {
    start_time,
    end_time,
    duration_min,
    step_min: EVA_STEP_MIN,
    top_k: EVA_TOP_K,
    critical_distance_km,
  };
}

export function evaWindowTone(window) {
  if (!window) return "unknown";
  if (window.blocked) return "danger";
  const status = String(window.status || "").toUpperCase();
  if (status === "SAFE") return "ok";
  if (status === "CAUTION" || status === "WATCH" || status === "ATTENTION" || status === "MARGINAL") {
    return "warn";
  }
  if (status === "UNSAFE" || status === "DANGER" || status === "CRITICAL" || status === "BLOCKED") {
    return "danger";
  }
  const danger = Number(window.peak_danger ?? window.danger);
  if (Number.isFinite(danger)) {
    if (danger >= 0.6) return "danger";
    if (danger >= 0.35) return "warn";
    return "ok";
  }
  return "unknown";
}

export function evaStatusText(window) {
  if (!window) return "Нет данных";
  if (window.blocked) return "Опасно";
  const status = String(window.status || "").toUpperCase();
  if (status === "SAFE") return "Безопасно";
  if (status === "CAUTION" || status === "WATCH" || status === "ATTENTION" || status === "MARGINAL") {
    return "Внимание";
  }
  if (status === "UNSAFE" || status === "DANGER" || status === "CRITICAL" || status === "BLOCKED") {
    return "Опасно";
  }
  const tone = evaWindowTone(window);
  if (tone === "ok") return "Безопасно";
  if (tone === "danger") return "Опасно";
  if (tone === "warn") return "Внимание";
  return "Нет данных";
}

export function overlayWindows(data) {
  const forecast = forecastOverlayWindows(data);
  if (forecast.length) return forecast;
  if (data?.eva?.windows?.length) return data.eva.windows;
  const rec = data?.recommendation?.window;
  return rec ? [{ start: rec.start, end: rec.end, status: "SAFE" }] : [];
}

export function evaBestWindow(data) {
  return data?.eva?.available ? data.eva.windows?.[0] || null : null;
}

function normalizeWindow(raw) {
  if (!raw) return null;
  const start = toIsoZ(raw.start);
  const end = toIsoZ(raw.end);
  if (!start || !end) return null;
  return {
    start,
    end,
    status: raw.status || "UNKNOWN",
    safety: raw.safety ?? null,
    danger: raw.danger ?? null,
    peak_danger: raw.peak_danger ?? null,
    average_danger: raw.average_danger ?? null,
    critical_overlap_seconds: raw.critical_overlap_seconds ?? null,
    adverse_duration_seconds: raw.adverse_duration_seconds ?? null,
    data_coverage: raw.data_coverage ?? null,
    eva_min: raw.eva_min ?? null,
    distance_min_km: raw.distance_min_km ?? null,
    blocked: Boolean(raw.blocked),
    factors: Array.isArray(raw.factors) ? raw.factors : [],
    reasons: Array.isArray(raw.reasons) ? raw.reasons.filter(Boolean) : [],
  };
}

function patchEvaSource(metadata, payload, error) {
  const sources = [...(metadata?.sources || [])];
  const name = "CosmoHACK EVA API";
  const fetched = payload?.data_quality?.calculated_at || null;
  const row = {
    name,
    url: "http://92.255.110.133:8001/docs",
    fetched_at: fetched,
    age: fetched ? ageLabel(fetched) : "—",
    status: error ? "error" : qualityStatus(payload?.data_quality?.status),
    enabled: true,
    frozen: false,
  };
  const idx = sources.findIndex((s) => s.name === name);
  if (idx >= 0) sources[idx] = { ...sources[idx], ...row };
  else sources.push(row);
  return { ...metadata, sources };
}

/**
 * Attach EVA ranking. Ranked slots replace mock equal-duration windows.
 */
export function applyEvaWindows(data, payload, error = null) {
  if (!data) return data;
  const warnings = [];
  const requested = Number(data.request?.critical_distance_km);
  if (Number.isFinite(requested) && requested > EVA_CRITICAL_MAX_KM) {
    warnings.push(
      `Порог сближения ${requested} км ограничен ${EVA_CRITICAL_MAX_KM} км для EVA API.`,
    );
  }
  if (error) {
    const message = error.message || "EVA API недоступен.";
    return {
      ...data,
      metadata: patchEvaSource(data.metadata, null, error),
      eva: {
        available: false,
        error: message,
        request: null,
        windows: [],
        horizon_minutes: null,
        data_quality: { status: "ERROR", warnings: [message] },
      },
    };
  }
  if (!payload) {
    return {
      ...data,
      eva: data.eva || { available: false, windows: [], error: null },
    };
  }

  const windows = (payload.windows || []).map(normalizeWindow).filter(Boolean);
  const dqWarnings = [...warnings, ...(payload.data_quality?.warnings || [])];
  const best = windows[0] || null;
  const rec = data.recommendation || {};

  return {
    ...data,
    metadata: patchEvaSource(data.metadata, payload, null),
    recommendation: {
      ...rec,
      window: best ? { start: best.start, end: best.end } : rec.window,
      score: best?.safety ?? rec.score,
    },
    windows: windows.length ? windows.map(evaWindowToRow).filter(Boolean) : data.windows,
    eva: {
      available: true,
      error: null,
      request: payload.request || null,
      horizon_minutes: payload.horizon_minutes ?? null,
      windows,
      data_quality: {
        ...(payload.data_quality || {}),
        warnings: dqWarnings,
      },
    },
  };
}
