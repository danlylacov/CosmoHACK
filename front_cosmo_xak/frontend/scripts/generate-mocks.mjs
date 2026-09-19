/**
 * Generates mock AnalyzeResponse / sources-status JSON with a simplified ISS orbit.
 * Run: npm run mocks
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const outDir = join(__dirname, "../src/api/mocks");

const ALGORITHM_VERSION = "eva-risk-0.9.2";
const START = "2024-06-15T08:00:00Z";
const DURATION_H = 6;
const PERIOD_H = 12;
const TRAJ_HOURS = 18;
const STEP_MIN = 2;

const round = (n, d = 4) => Number(n.toFixed(d));

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

function generateTrajectory(startISO, hours, stepMin) {
  const start = Date.parse(startISO);
  const inc = (51.64 * Math.PI) / 180;
  const periodSec = 92.68 * 60;
  const omega = (2 * Math.PI) / periodSec;
  const mu0 = 0.85;
  const raan0 = 2.15;
  const earthRot = 7.2921159e-5;
  const n = Math.floor((hours * 60) / stepMin) + 1;
  const points = [];

  for (let i = 0; i < n; i++) {
    const tSec = i * stepMin * 60;
    const u = mu0 + omega * tSec;
    const lat = (Math.asin(Math.sin(inc) * Math.sin(u)) * 180) / Math.PI;
    const lonInertial = Math.atan2(Math.cos(inc) * Math.sin(u), Math.cos(u));
    let lon = ((lonInertial + raan0 - earthRot * tSec) * 180) / Math.PI;
    lon = ((((lon + 180) % 360) + 360) % 360) - 180;

    const sunLon = -15 * (tSec / 3600);
    const sunLat = 23.4;
    const ang = sphericalDistanceDeg(lat, lon, sunLat, sunLon);
    const in_shadow = ang > 105;

    points.push({
      t: toISO(start + tSec * 1000),
      lat: round(lat, 4),
      lon: round(lon, 4),
      alt_km: round(417.2 + Math.sin(tSec / 1800) * 3.4, 2),
      in_shadow,
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

function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

function series(hours, fn, step = 0.25) {
  const out = [];
  for (let t = 0; t <= hours + 1e-9; t += step) {
    out.push({ t: round(t, 2), risk: round(clamp01(fn(t)), 3) });
  }
  return out;
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

function windowMetrics(startH, duration, periodStartMs) {
  const endH = startH + duration;
  const samples = [];
  for (let t = startH; t <= endH; t += 0.25) samples.push(t);
  const avg = (fn) =>
    samples.reduce((s, t) => s + fn(t), 0) / samples.length;
  const overlap = (fn, thr) => {
    let h = 0;
    for (let i = 0; i < samples.length - 1; i++) {
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
    clamp01(1 - 0.55 * rsw - 0.45 * rm - 0.04 * overlap_sw - 0.03 * overlap_mmod) *
      completeness,
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

const traj = generateTrajectory(START, TRAJ_HOURS, STEP_MIN);
const startMs = Date.parse(START);

const windows = [0, 2, 4, 6, 8].map((h) => windowMetrics(h, DURATION_H, startMs));
windows.sort((a, b) => b.score - a.score);
const winner = windows[0];

const analyze = {
  query_id: "q-20240615-0800-a7c2",
  status: "success",
  algorithm_version: ALGORITHM_VERSION,
  request: {
    start: START,
    duration: DURATION_H,
    period: PERIOD_H,
    mode: "historical",
    cutoff_time: "2024-06-15T23:50:00Z",
  },
  orbit: {
    tle_source: "CelesTrak NORAD (ISS ZARYA 25544)",
    tle_epoch: "2024-06-15T06:12:41Z",
    tle_age_hours: 1.79,
    iss_trajectory: traj,
    shadow_intervals: shadowIntervals(traj),
  },
  risk_sw: {
    risk_sw: round(
      windows.reduce((s, w) => s + w.risk_sw, 0) / windows.length,
      3,
    ),
    components: { sep: 0.11, cme: 0.34, kp: 0.22, flare: 0.08 },
    time_series: series(TRAJ_HOURS, swRisk),
    metrics: { kp: 4.3, sep_10mev: 2.1, cme_speed: 680 },
    warnings: [
      {
        id: "w-sw-cme-donki",
        type: "forecast",
        mechanism: "radiation",
        title: "Приход CME: повышенный риск радиации",
        source: "NASA DONKI / NOAA SWPC WSA-Enlil",
        published_at: "2024-06-14T21:10:00Z",
        event_time: "2024-06-15T17:30:00Z",
        period: { start: "2024-06-15T15:30:00Z", end: "2024-06-15T19:30:00Z" },
        value: 680,
        unit: "km/s",
        impact:
          "Ожидается рост Kp и возможный рост SEP. Пересечение с поздними окнами ВКД.",
        rule: "risk_sw = max(w_sep·SEP, w_cme·CME, w_kp·Kp, w_flare·flare); порог «внимание» 0.35, «опасно» 0.6",
        limitations: "Время прихода CME ±2 ч (WSA-Enlil).",
        confidence: "medium",
        intersects_window: true,
      },
      {
        id: "w-sw-kp",
        type: "observation",
        mechanism: "radiation",
        title: "Kp = 4.3 (G1 на границе)",
        source: "NOAA SWPC Planetary K-index",
        published_at: "2024-06-15T07:45:00Z",
        event_time: "2024-06-15T07:00:00Z",
        period: { start: "2024-06-15T06:00:00Z", end: "2024-06-15T09:00:00Z" },
        value: 4.3,
        unit: "Kp",
        impact: "Умеренная геомагнитная активность; вклад в risk_sw через компонент kp.",
        rule: "kp_norm = clamp((Kp − 3) / 6, 0, 1)",
        limitations: "Kp — 3-часовой индекс, внутри окна возможны краткие пики.",
        confidence: "high",
        intersects_window: true,
      },
      {
        id: "w-sw-sep",
        type: "observation",
        mechanism: "radiation",
        title: "SEP > 10 MeV в норме",
        source: "NOAA GOES proton flux",
        published_at: "2024-06-15T07:50:00Z",
        event_time: null,
        period: { start: "2024-06-15T00:00:00Z", end: "2024-06-15T08:00:00Z" },
        value: 2.1,
        unit: "pfu",
        impact: "Поток протонов ниже порога радиационного шторма S1 (10 pfu).",
        rule: "sep_norm = clamp(log10(pfu / 1) / 3, 0, 1)",
        limitations: "Запаздывание GOES относительно вспышки ~15–60 мин.",
        confidence: "high",
        intersects_window: false,
      },
    ],
  },
  risk_mmod: {
    risk_mmod: 0.21,
    components: { conjunction: 0.31, meteor: 0.12 },
    time_series: series(TRAJ_HOURS, mmodRisk),
    metrics: {
      miss_distance: 4.8,
      pc: 1.2e-5,
      tca: "2024-06-15T15:12:00Z",
    },
    warnings: [
      {
        id: "w-mmod-conj",
        type: "computed",
        mechanism: "mmod",
        title: "Сближение: TCA 15:12 UTC, miss 4.8 км",
        source: "Space-Track CDM / расчёт прототипа",
        published_at: "2024-06-15T04:20:00Z",
        event_time: "2024-06-15T15:12:00Z",
        period: { start: "2024-06-15T14:40:00Z", end: "2024-06-15T15:40:00Z" },
        value: 4.8,
        unit: "km",
        impact: "Повышенный risk_mmod в середине поискового периода; ранние окна предпочтительнее.",
        rule: "conjunction = 1 − sigmoid((miss_km − 2) / 2) · (1 − min(1, Pc / 1e-4))",
        limitations: "CDM обновляется; miss distance и Pc могут измениться до TCA.",
        confidence: "medium",
        intersects_window: true,
      },
      {
        id: "w-mmod-meteor",
        type: "forecast",
        mechanism: "mmod",
        title: "Дневные Ариетиды: фоновый метеорный поток",
        source: "IAU Meteor Data Center",
        published_at: "2024-06-01T00:00:00Z",
        event_time: null,
        period: { start: "2024-06-14T00:00:00Z", end: "2024-06-16T00:00:00Z" },
        value: 12,
        unit: "ZHR",
        impact: "Небольшой вклад в компонент meteor; не является определяющим фактором.",
        rule: "meteor = min(1, ZHR / 80) · exposure_hours / 8",
        limitations: "ZHR для наземного наблюдателя; для МКС — оценка порядка величины.",
        confidence: "low",
        intersects_window: true,
      },
    ],
  },
  windows,
  recommendation: {
    window: { start: winner.start, end: winner.end },
    score: winner.score,
    reason: "Низкий риск радиации и отсутствие сближений MMOD",
    limitations: "CME arrival ±2 ч; CDM может обновиться до TCA.",
  },
  metadata: {
    completeness: 0.94,
    confidence_sw: "средняя",
    confidence_mmod: "средняя",
    algorithm_version: ALGORITHM_VERSION,
    sources: [
      {
        name: "NOAA SWPC (Kp, GOES protons)",
        url: "https://services.swpc.noaa.gov/",
        fetched_at: "2024-06-15T07:52:00Z",
        age: "8 мин",
        status: "ok",
      },
      {
        name: "NASA DONKI (CME / WSA-Enlil)",
        url: "https://kauai.ccmc.gsfc.nasa.gov/DONKI/",
        fetched_at: "2024-06-15T07:40:00Z",
        age: "20 мин",
        status: "ok",
      },
      {
        name: "CelesTrak TLE ISS (25544)",
        url: "https://celestrak.org/NORAD/elements/",
        fetched_at: "2024-06-15T07:30:00Z",
        age: "30 мин",
        status: "ok",
      },
      {
        name: "Space-Track CDM",
        url: "https://www.space-track.org/",
        fetched_at: "2024-06-15T04:20:00Z",
        age: "3.7 ч",
        status: "stale",
      },
      {
        name: "IAU Meteor Data Center",
        url: "https://www.ta3.sk/IAUC22DB/MDC2007/",
        fetched_at: "2024-06-15T00:00:00Z",
        age: "8 ч",
        status: "ok",
      },
    ],
  },
};

const sourcesStatus = {
  status: "success",
  algorithm_version: ALGORITHM_VERSION,
  completeness: 0.94,
  confidence_sw: "средняя",
  confidence_mmod: "средняя",
  refreshed_at: "2024-06-15T07:52:00Z",
  sources: analyze.metadata.sources,
};

const errorResponse = {
  status: "error",
  message: "Источник NOAA SWPC недоступен. Расчёт risk_sw не выполнен.",
  code: "SOURCE_UNAVAILABLE",
  algorithm_version: ALGORITHM_VERSION,
};

const tle = {
  status: "success",
  algorithm_version: ALGORITHM_VERSION,
  name: "ISS (ZARYA)",
  norad_id: 25544,
  source: "CelesTrak NORAD",
  epoch: "2024-06-15T06:12:41Z",
  age_hours: 1.79,
  line1: "1 25544U 98067A   24167.25880787  .00016717  00000-0  10270-3 0  9993",
  line2: "2 25544  51.6416 123.4521 0003456  82.1234  41.8765 15.50012345678901",
};

await mkdir(outDir, { recursive: true });
await writeFile(join(outDir, "analyze-response.json"), JSON.stringify(analyze, null, 2));
await writeFile(join(outDir, "sources-status.json"), JSON.stringify(sourcesStatus, null, 2));
await writeFile(join(outDir, "error-response.json"), JSON.stringify(errorResponse, null, 2));
await writeFile(join(outDir, "tle-response.json"), JSON.stringify(tle, null, 2));

console.log(
  `Wrote mocks: traj=${traj.length} pts, shadows=${analyze.orbit.shadow_intervals.length}, windows=${windows.length}`,
);
