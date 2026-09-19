import { http, HttpResponse, delay } from "msw";
import { API_PREFIX } from "../../config.js";
import analyzeTemplate from "./analyze-response.json";
import sourcesStatus from "./sources-status.json";
import errorResponse from "./error-response.json";
import tleResponse from "./tle-response.json";

const reports = new Map();
let sources = normalizeSources(structuredClone(sourcesStatus));
let statusPolls = 0;

function normalizeSources(payload) {
  return {
    ...payload,
    sources: (payload.sources || []).map((s) => ({
      enabled: true,
      frozen: false,
      unsuitable_for_replay: Boolean(s.name?.includes("IAU")),
      ...s,
    })),
  };
}

function attachWarningUrls(warning) {
  const name = String(warning.source || "").toLowerCase();
  let source_url = warning.source_url ?? null;
  if (!source_url) {
    if (name.includes("donki")) source_url = "https://kauai.ccmc.gsfc.nasa.gov/DONKI/";
    else if (name.includes("swpc") || name.includes("goes") || name.includes("noaa")) {
      source_url = "https://services.swpc.noaa.gov/";
    } else if (name.includes("space-track") || name.includes("cdm")) {
      source_url = "https://www.space-track.org/";
    } else if (name.includes("iau")) source_url = "https://www.ta3.sk/IAUC22DB/MDC2007/";
    else if (name.includes("celestrak")) source_url = "https://celestrak.org/NORAD/elements/";
  }
  return { ...warning, source_url };
}

const round = (n, d = 4) => Number(n.toFixed(d));
const clamp01 = (x) => Math.max(0, Math.min(1, x));

function toISO(ms) {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

function sphericalDistanceDeg(lat1, lon1, lat2, lon2) {
  const d2r = Math.PI / 180;
  const cosc =
    Math.sin(lat1 * d2r) * Math.sin(lat2 * d2r) +
    Math.cos(lat1 * d2r) * Math.cos(lat2 * d2r) * Math.cos((lon1 - lon2) * d2r);
  return Math.acos(Math.min(1, Math.max(-1, cosc))) / d2r;
}

function generateTrajectory(startISO, hours, stepMin = 2) {
  const start = Date.parse(startISO);
  const inc = (51.64 * Math.PI) / 180;
  const periodSec = 92.68 * 60;
  const omega = (2 * Math.PI) / periodSec;
  const mu0 = 0.85;
  const raan0 = 2.15;
  const earthRot = 7.2921159e-5;
  const n = Math.floor((hours * 60) / stepMin) + 1;
  const points = [];
  for (let i = 0; i < n; i += 1) {
    const tSec = i * stepMin * 60;
    const u = mu0 + omega * tSec;
    const lat = (Math.asin(Math.sin(inc) * Math.sin(u)) * 180) / Math.PI;
    const lonInertial = Math.atan2(Math.cos(inc) * Math.sin(u), Math.cos(u));
    let lon = ((lonInertial + raan0 - earthRot * tSec) * 180) / Math.PI;
    lon = ((((lon + 180) % 360) + 360) % 360) - 180;
    const sunLon = -15 * (tSec / 3600);
    const ang = sphericalDistanceDeg(lat, lon, 23.4, sunLon);
    points.push({
      t: toISO(start + tSec * 1000),
      lat: round(lat, 4),
      lon: round(lon, 4),
      alt_km: round(417.2 + Math.sin(tSec / 1800) * 3.4, 2),
      in_shadow: ang > 105,
    });
  }
  return points;
}

function shadowIntervals(points) {
  const intervals = [];
  let open = null;
  for (const p of points) {
    if (p.in_shadow && !open) open = p.t;
    if (!p.in_shadow && open) {
      intervals.push([open, p.t]);
      open = null;
    }
  }
  if (open) intervals.push([open, points[points.length - 1].t]);
  return intervals;
}

function swRisk(t) {
  const cme = 0.08 + 0.55 * Math.exp(-((t - 9.5) ** 2) / 8);
  const kp = 0.12 + 0.18 * Math.sin((t / 18) * Math.PI);
  const sep = 0.05 + 0.08 * Math.exp(-((t - 10) ** 2) / 12);
  const flare = 0.04 + (t > 14 ? 0.1 : 0);
  return Math.max(cme, kp, sep, flare);
}

function mmodRisk(t) {
  const conj = 0.62 * Math.exp(-((t - 7.2) ** 2) / 1.8);
  const meteor = 0.1 + 0.06 * Math.sin((t / 6) * Math.PI);
  return Math.max(conj, meteor);
}

function makeSeries(hours, fn, step = 0.25) {
  const out = [];
  for (let t = 0; t <= hours + 1e-9; t += step) {
    out.push({ t: round(t, 2), risk: round(clamp01(fn(t)), 3) });
  }
  return out;
}

function windowMetrics(startH, duration, periodStartMs) {
  const endH = startH + duration;
  const samples = [];
  for (let t = startH; t <= endH + 1e-9; t += 0.25) samples.push(t);
  const avg = (fn) => samples.reduce((s, t) => s + fn(t), 0) / Math.max(1, samples.length);
  const overlap = (fn, thr) => {
    let h = 0;
    for (let i = 0; i < samples.length - 1; i += 1) {
      if (fn(samples[i]) >= thr) h += 0.25;
    }
    return round(h, 2);
  };
  const rsw = round(avg(swRisk), 3);
  const rm = round(avg(mmodRisk), 3);
  const overlap_sw = overlap(swRisk, 0.35);
  const overlap_mmod = overlap(mmodRisk, 0.3);
  const completeness = startH >= 10 ? 0.78 : 0.96;
  const score = round(
    clamp01(1 - 0.55 * rsw - 0.45 * rm - 0.04 * overlap_sw - 0.03 * overlap_mmod) * completeness,
    3,
  );
  return {
    start: toISO(periodStartMs + startH * 3600_000),
    end: toISO(periodStartMs + endH * 3600_000),
    score,
    risk_sw: rsw,
    risk_mmod: rm,
    overlap_sw,
    overlap_mmod,
    completeness,
    requires_check: completeness < 0.9 || rsw > 0.4 || rm > 0.35,
  };
}

function buildWindows(startIso, duration, period) {
  const startMs = Date.parse(startIso);
  const span = Math.max(0, period - duration);
  const count = Math.min(8, Math.max(3, Math.round(span / Math.max(duration, 1)) + 1));
  const step = count <= 1 ? 0 : span / (count - 1);
  const windows = [];
  for (let i = 0; i < count; i += 1) {
    windows.push(windowMetrics(round(i * step, 2), duration, startMs));
  }
  windows.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  return windows;
}

function overlayRequest(template, body) {
  const data = structuredClone(template);
  const start = body?.start || data.request.start;
  const duration = Number(body?.duration ?? data.request.duration);
  const period = Number(body?.period ?? data.request.period);
  const mode = body?.mode || data.request.mode;
  const historical_intent = mode === "historical" ? body?.historical_intent || "replay" : null;
  const disabled = new Set(body?.disabled_sources || []);
  const frozen = new Set(body?.frozen_sources || []);
  const deltaMs = Date.parse(start) - Date.parse(template.request.start);

  const shiftIso = (iso) => {
    if (!iso) return null;
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return iso;
    return new Date(t + deltaMs).toISOString().replace(/\.\d{3}Z$/, "Z");
  };

  const cutoff_time =
    historical_intent === "replay" ? body?.cutoff_time || start : null;

  data.request = {
    mode,
    start,
    duration,
    period,
    historical_intent,
    cutoff_time,
    disabled_sources: [...disabled],
    frozen_sources: [...frozen],
  };

  const coverHours = Math.max(period, duration, 1);
  const traj = generateTrajectory(start, coverHours);
  data.orbit.iss_trajectory = traj;
  data.orbit.shadow_intervals = shadowIntervals(traj);
  data.orbit.tle_epoch = shiftIso(data.orbit.tle_epoch);
  data.orbit.geometry_kind = historical_intent === "review" ? "reconstruction" : "operational";

  data.risk_sw.availability = "ok";
  data.risk_mmod.availability = "ok";
  data.risk_sw.time_series = makeSeries(coverHours, swRisk);
  data.risk_mmod.time_series = makeSeries(coverHours, mmodRisk);

  const windows = buildWindows(start, duration, period);
  data.windows = windows;
  const winner = windows[0] || null;
  data.recommendation.tie = false;
  data.recommendation.window = winner ? { start: winner.start, end: winner.end } : null;
  data.recommendation.score = winner?.score ?? 0;
  data.risk_sw.risk_sw = round(
    windows.reduce((s, w) => s + (w.risk_sw ?? 0), 0) / Math.max(1, windows.length),
    3,
  );
  data.risk_mmod.risk_mmod = round(
    windows.reduce((s, w) => s + (w.risk_mmod ?? 0), 0) / Math.max(1, windows.length),
    3,
  );

  data.risk_mmod.metrics.tca = shiftIso(data.risk_mmod.metrics.tca);

  const shiftWarning = (w) =>
    attachWarningUrls({
      ...w,
      published_at: shiftIso(w.published_at),
      event_time: w.event_time ? shiftIso(w.event_time) : null,
      period: w.period
        ? { start: shiftIso(w.period.start), end: shiftIso(w.period.end) }
        : null,
    });
  data.risk_sw.warnings = data.risk_sw.warnings.map(shiftWarning);
  data.risk_mmod.warnings = data.risk_mmod.warnings.map(shiftWarning);

  if (data.verification?.later_observations) {
    data.verification = {
      ...data.verification,
      cutoff_time: shiftIso(data.verification.cutoff_time) || cutoff_time,
      later_observations: data.verification.later_observations.map(shiftWarning),
    };
  }

  data.metadata.sources = (data.metadata.sources || sources.sources).map((s) => ({
    enabled: true,
    frozen: frozen.has(s.name),
    unsuitable_for_replay: Boolean(s.name?.includes("IAU")),
    ...s,
    frozen: frozen.has(s.name),
    enabled: !disabled.has(s.name),
  }));

  if (historical_intent === "replay") {
    const afterCutoff = (w) =>
      cutoff_time && Date.parse(w.published_at) > Date.parse(cutoff_time);

    const pulled = [...data.risk_sw.warnings, ...data.risk_mmod.warnings]
      .filter(afterCutoff)
      .map((w) => ({
        ...w,
        id: `${w.id}-after-cutoff`,
        limitations: w.limitations
          ? `${w.limitations} Поздний факт, в оценку не входил.`
          : "Поздний факт, в оценку не входил.",
      }));

    const archived = (data.verification?.later_observations || []).filter(afterCutoff);
    const seen = new Set();
    const later = [...archived, ...pulled].filter((w) => {
      if (seen.has(w.id)) return false;
      seen.add(w.id);
      return true;
    });

    data.verification = {
      cutoff_time,
      used_in_calculation: false,
      note:
        data.verification?.note ||
        "Поздние уточнения не входят в оценку и показаны отдельно.",
      later_observations: later,
    };
    data.risk_sw.warnings = data.risk_sw.warnings.filter((w) => !afterCutoff(w));
    data.risk_mmod.warnings = data.risk_mmod.warnings.filter((w) => !afterCutoff(w));
  } else {
    data.verification = null;
  }

  if (mode === "historical" && start.startsWith("2024-05-15")) {
    data.status = "partial";
    data.metadata.completeness = 0.62;
    data.metadata.confidence_sw = "низкая";
    data.risk_sw.availability = "insufficient_data";
    data.recommendation.window = null;
    data.recommendation.score = 0;
    data.recommendation.reason =
      "Недостаточно оснований для выбора. Требуется дополнительная проверка.";
    data.recommendation.limitations =
      "NOAA SWPC недоступен; risk_sw рассчитан по неполным архивам.";
    data.metadata.sources = data.metadata.sources.map((s) =>
      s.name.includes("NOAA") ? { ...s, status: "error", age: "н/д", enabled: false } : s,
    );
  }

  const swOff = [...disabled].some((n) => /noaa|donki|swpc/i.test(n));
  const mmodOff = [...disabled].some((n) => /space-track|iau|cdm/i.test(n));
  if (swOff) {
    data.risk_sw.availability = "disabled";
    data.status = "partial";
    data.metadata.confidence_sw = "низкая";
    data.recommendation.window = null;
    data.recommendation.reason =
      "Недостаточно оснований для выбора: механизм SW отключён. Отсутствие оценки ≠ нулевой риск.";
    data.metadata.completeness = Math.min(data.metadata.completeness, 0.5);
  }
  if (mmodOff) {
    data.risk_mmod.availability = "disabled";
    data.status = "partial";
    data.metadata.confidence_mmod = "низкая";
    data.recommendation.window = null;
    data.metadata.completeness = Math.min(data.metadata.completeness, 0.5);
  }

  const ranked = data.windows.slice().sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  if (ranked.length >= 2 && Math.abs((ranked[0].score ?? 0) - (ranked[1].score ?? 0)) < 0.01) {
    data.recommendation.tie = true;
    if (!swOff && !mmodOff && data.recommendation.window) {
      data.recommendation.reason =
        "Равнозначные окна по score. Предпочтение за более полной обеспеченностью данных, не за «тишину» в одном механизме.";
    }
  }

  data.query_id = `q-${start.replace(/[-:]/g, "").slice(0, 15)}-${Math.random()
    .toString(16)
    .slice(2, 6)}`;
  return data;
}

export const handlers = [
  http.post(`${API_PREFIX}/analyze`, async ({ request }) => {
    await delay(700);
    const body = await request.json().catch(() => null);
    if (!body?.start || !body?.mode) {
      return HttpResponse.json(errorResponse, { status: 400 });
    }
    const data = overlayRequest(analyzeTemplate, body);
    if (data.status !== "partial") {
      data.metadata.completeness = sources.completeness;
      data.metadata.confidence_sw = sources.confidence_sw;
      data.metadata.confidence_mmod = sources.confidence_mmod;
    }
    reports.set(data.query_id, data);
    return HttpResponse.json(data);
  }),

  http.get(`${API_PREFIX}/iss/tle`, async () => {
    await delay(200);
    return HttpResponse.json(tleResponse);
  }),

  http.get(`${API_PREFIX}/sources/status`, async () => {
    await delay(250);
    statusPolls += 1;
    if (statusPolls % 2 === 0) {
      const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
      sources = {
        ...sources,
        refreshed_at: now,
        sources: sources.sources.map((s) =>
          s.frozen || s.name.includes("IAU")
            ? s
            : { ...s, fetched_at: now, age: "только что" },
        ),
      };
    }
    return HttpResponse.json(sources);
  }),

  http.post(`${API_PREFIX}/sources/refresh`, async ({ request }) => {
    await delay(500);
    const body = await request.json().catch(() => ({}));
    const frozen = new Set(body?.frozen_sources || []);
    const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
    sources = {
      ...sources,
      refreshed_at: now,
      sources: sources.sources.map((s) =>
        frozen.has(s.name)
          ? { ...s, frozen: true }
          : {
              ...s,
              frozen: false,
              fetched_at: now,
              age: "только что",
              status: s.status === "stale" ? "ok" : s.status,
            },
      ),
    };
    return HttpResponse.json(sources);
  }),

  http.get(`${API_PREFIX}/report/:queryId`, async ({ params }) => {
    await delay(200);
    const data = reports.get(params.queryId);
    if (!data) {
      return HttpResponse.json(
        {
          status: "error",
          message: "Отчёт не найден. Сначала выполните расчёт.",
          code: "REPORT_NOT_FOUND",
          algorithm_version: errorResponse.algorithm_version,
        },
        { status: 404 },
      );
    }
    return HttpResponse.json(data);
  }),
];
