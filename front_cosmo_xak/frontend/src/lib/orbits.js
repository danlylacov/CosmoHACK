import { addHoursIso, formatNumber, formatUtc } from "./format.js";
import { temeToGeodetic, temeDistanceKm } from "./teme.js";

const DOCKED_KM = 5;
const PERIOD_RANGE_MAX = 24;

function toIsoZ(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Form start + search period → orbit API window (0, 24] h, timezone included. */
export function toOrbitWindow(query) {
  const start_time = toIsoZ(query?.start);
  const period = Number(query?.period);
  const duration = Number(query?.duration);
  const hours = Math.min(
    PERIOD_RANGE_MAX,
    Math.max(1, Number.isFinite(period) ? period : Number.isFinite(duration) ? duration : 1),
  );
  return {
    start_time,
    end_time: addHoursIso(start_time, hours),
  };
}

/** Subsolar point at an instant — terminator longitude moves 15° per hour. */
export function subsolarPoint(iso) {
  const d = iso ? new Date(iso) : new Date();
  if (Number.isNaN(d.getTime())) return { lat: 0, lon: 0 };
  const start = Date.UTC(d.getUTCFullYear(), 0, 0);
  const doy = (d.getTime() - start) / 86400000;
  const lat = 23.44 * Math.sin(((2 * Math.PI) / 365) * (doy - 81));
  const utcHours = d.getUTCHours() + d.getUTCMinutes() / 60 + d.getUTCSeconds() / 3600;
  const lon = ((-15 * (utcHours - 12) + 180) % 360) - 180;
  return { lat, lon };
}

function zenithDeg(lat, lon, iso) {
  const d2r = Math.PI / 180;
  const sun = subsolarPoint(iso);
  const cosc =
    Math.sin(lat * d2r) * Math.sin(sun.lat * d2r) +
    Math.cos(lat * d2r) * Math.cos(sun.lat * d2r) * Math.cos((lon - sun.lon) * d2r);
  return (Math.acos(Math.min(1, Math.max(-1, cosc))) * 180) / Math.PI;
}

function smoothstep(edge0, edge1, x) {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0 || 1)));
  return t * t * (3 - 2 * t);
}

/** 1 = full daylight, 0 = night. Twilight band ~86–108° (civil→astronomical). */
export function sunlitAmount(lat, lon, iso) {
  return 1 - smoothstep(86, 108, zenithDeg(lat, lon, iso));
}

export function inShadow(lat, lon, iso) {
  return zenithDeg(lat, lon, iso) > 96;
}

function pointFromState(state, iso) {
  const p = state?.position_km;
  if (!p) return null;
  const geo = temeToGeodetic(p.x, p.y, p.z, iso);
  return {
    t: iso,
    lat: geo.lat,
    lon: geo.lon,
    alt_km: geo.alt_km,
    in_shadow: inShadow(geo.lat, geo.lon, iso),
    norad_id: state.norad_id ?? null,
    name: state.name || null,
  };
}

function screenedAt(sample) {
  if (Array.isArray(sample.screened_objects)) return sample.screened_objects;
  if (sample.nearest_object) return [sample.nearest_object];
  return [];
}

function isDocked(object, iss) {
  return temeDistanceKm(object?.position_km, iss?.position_km) < DOCKED_KM;
}

export function positionsToTrajectories(payload) {
  const samples = payload?.samples || [];
  const iss_trajectory = [];
  const debrisById = new Map();
  let closest = null;
  let closestKm = Infinity;

  for (const sample of samples) {
    const iso = sample.timestamp;
    const issPt = pointFromState(sample.iss, iso);
    if (issPt) iss_trajectory.push(issPt);

    for (const obj of screenedAt(sample)) {
      if (isDocked(obj, sample.iss) || !obj?.position_km) continue;
      const pt = pointFromState(obj, iso);
      if (!pt) continue;
      const dist = temeDistanceKm(obj.position_km, sample.iss?.position_km);
      if (dist < closestKm) {
        closestKm = dist;
        closest = { ...pt, distance_km: Number(dist.toFixed(2)) };
      }
      const id = obj.norad_id ?? obj.name ?? `obj-${debrisById.size}`;
      if (!debrisById.has(id)) {
        debrisById.set(id, {
          norad_id: obj.norad_id ?? null,
          name: obj.name || (obj.norad_id != null ? `NORAD ${obj.norad_id}` : "объект"),
          object_type: obj.object_type ?? null,
          points: [],
        });
      }
      debrisById.get(id).points.push({ ...pt, distance_km: Number(dist.toFixed(2)) });
    }
  }

  return {
    iss_trajectory,
    debris: [...debrisById.values()],
    closest_approach: closest,
  };
}

export function debrisTrackId(track, index = 0) {
  if (track?.norad_id != null) return String(track.norad_id);
  if (track?.name) return String(track.name);
  return `obj-${index}`;
}

export function debrisMissKm(track) {
  if (track?.miss_km != null && Number.isFinite(Number(track.miss_km))) return Number(track.miss_km);
  const ds = (track?.points || [])
    .map((p) => p.distance_km)
    .filter((d) => d != null && Number.isFinite(Number(d)));
  return ds.length ? Math.min(...ds) : null;
}

export function syncDebrisVisible(debris, prev = {}) {
  const next = {};
  (debris || []).forEach((track, i) => {
    const id = debrisTrackId(track, i);
    next[id] = prev[id] !== false;
  });
  return next;
}

function round(n, d = 3) {
  return Number(n.toFixed(d));
}

function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

export const CRITICAL_DISTANCE_KM = 5;

/** 1 at 0 km, 0.5 at the critical threshold, → 0 far away. */
export function distanceToMmodRisk(km, criticalKm = CRITICAL_DISTANCE_KM) {
  if (km == null || !Number.isFinite(Number(km))) return 0;
  const scale = Math.max(Number(criticalKm) / 2, 0.5);
  const x = (Number(km) - Number(criticalKm)) / scale;
  return round(clamp01(1 / (1 + Math.exp(x))), 3);
}

function stretchDistanceRows(rows) {
  const dists = rows.map((r) => r.distance_km).filter((d) => d != null);
  const dMin = dists.length ? Math.min(...dists) : 0;
  const dMax = dists.length ? Math.max(...dists) : 0;
  const span = Math.max(dMax - dMin, 1e-6);
  return rows.map((r) => {
    if (r.distance_km == null) return { t: r.t, risk: 0, distance_km: null };
    return {
      t: r.t,
      distance_km: r.distance_km,
      risk: round(0.08 + 0.92 * clamp01((dMax - r.distance_km) / span), 3),
    };
  });
}

/** Min distance to non-docked screened objects, hours from start, 0–1 proximity for the chart. */
export function debrisDistanceSeries(payload, startIso) {
  const start = Date.parse(startIso);
  const samples = payload?.samples || [];
  const rows = [];
  for (const sample of samples) {
    let minD = Infinity;
    for (const obj of screenedAt(sample)) {
      if (isDocked(obj, sample.iss)) continue;
      const d = temeDistanceKm(obj.position_km, sample.iss?.position_km);
      if (d < minD) minD = d;
    }
    const t = Number.isFinite(start) ? (Date.parse(sample.timestamp) - start) / 3600_000 : 0;
    rows.push({
      t: round(t, 4),
      distance_km: Number.isFinite(minD) ? round(minD, 1) : null,
    });
  }
  return stretchDistanceRows(rows);
}

export function seriesPointAt(series, t) {
  if (!series?.length) return { t: 0, risk: 0 };
  if (t <= series[0].t) return series[0];
  for (let i = 1; i < series.length; i += 1) {
    if (t <= series[i].t) {
      const a = series[i - 1];
      const b = series[i];
      const u = (t - a.t) / (b.t - a.t || 1);
      const out = { ...a, t };
      for (const key of Object.keys(a)) {
        if (typeof a[key] === "number" && typeof b[key] === "number") {
          out[key] = a[key] + (b[key] - a[key]) * u;
        }
      }
      return out;
    }
  }
  return series[series.length - 1];
}

export function formatChartNow(key, series, t) {
  if (!series?.length) return "н/д";
  const p = seriesPointAt(series, t);
  if (key === "mmod" && p.distance_km != null && Number.isFinite(Number(p.distance_km))) {
    return `${formatNumber(p.distance_km, 0)} км`;
  }
  const risk = formatNumber(p.risk ?? 0, 2);
  if (key === "sw" && p.eva_allowed === true) return `${risk} · разрешён`;
  if (key === "sw" && p.eva_allowed === false) return `${risk} · запрещён`;
  return risk;
}

export function chartDotXY(series, t, maxT, w = 1000, h = 220) {
  const padT = 14;
  const padB = 14;
  const innerH = h - padT - padB;
  const p = seriesPointAt(series, t);
  return {
    x: (Number(t) / (maxT || 1)) * w,
    y: padT + (1 - (p.risk ?? 0)) * innerH,
  };
}

const PLAY_EPS = 1e-6;

function hoursOfIso(iso, startIso) {
  return (Date.parse(iso) - Date.parse(startIso)) / 3600_000;
}

/** Hours from window start at which playback must pause (TCA and critical intervals). */
export function playbackStopHours(data) {
  const startIso = data?.request?.start;
  const maxT = Math.max(1, Number(data?.request?.period) || 0);
  if (!startIso || Number.isNaN(Date.parse(startIso))) return [];
  const hours = [];
  const pushIso = (iso) => {
    if (!iso) return;
    const h = hoursOfIso(iso, startIso);
    if (Number.isFinite(h) && h > PLAY_EPS && h < maxT - PLAY_EPS) hours.push(h);
  };
  pushIso(data?.orbit?.conjunction?.summary?.tca ?? data?.risk_mmod?.metrics?.tca);
  for (const iv of data?.orbit?.conjunction?.critical_intervals || []) {
    pushIso(iv.tca || iv.start_time || iv.start);
  }
  hours.sort((a, b) => a - b);
  const out = [];
  for (const h of hours) {
    if (!out.length || h - out[out.length - 1] > 1 / 3600) out.push(h);
  }
  return out;
}

/** Advance simulated hours; pause on the next conjunction stop or at the window end. */
export function stepPlayback(fromHours, dtHours, stops, maxT) {
  const from = Number(fromHours) || 0;
  const to = from + Number(dtHours);
  const cap = Math.max(0, Number(maxT) || 0);
  const nextStop = (stops || []).find((h) => h > from + PLAY_EPS && h <= to + PLAY_EPS);
  if (nextStop != null) return { t: nextStop, pause: true, reason: "conjunction" };
  if (to >= cap - PLAY_EPS) return { t: cap, pause: true, reason: "end" };
  return { t: to, pause: false, reason: null };
}

function lerpLon(a, b, u) {
  let d = b - a;
  if (d > 180) d -= 360;
  if (d < -180) d += 360;
  let lon = a + d * u;
  if (lon > 180) lon -= 360;
  if (lon < -180) lon += 360;
  return lon;
}

/** Interpolate a lat/lon/alt track to an absolute timestamp. */
export function pointAtTime(pts, tMs) {
  if (!pts?.length) return null;
  const t0 = Date.parse(pts[0].t);
  if (pts.length === 1 || !Number.isFinite(tMs) || tMs <= t0) return pts[0];
  for (let i = 1; i < pts.length; i += 1) {
    const a = pts[i - 1];
    const b = pts[i];
    const ta = Date.parse(a.t);
    const tb = Date.parse(b.t);
    if (tMs <= tb) {
      const u = (tMs - ta) / (tb - ta || 1);
      return {
        ...b,
        lat: a.lat + (b.lat - a.lat) * u,
        lon: lerpLon(a.lon, b.lon, u),
        alt_km: a.alt_km + (b.alt_km - a.alt_km) * u,
        distance_km:
          a.distance_km != null && b.distance_km != null
            ? a.distance_km + (b.distance_km - a.distance_km) * u
            : b.distance_km,
      };
    }
  }
  return pts[pts.length - 1];
}

function shadowIntervalsFromPoints(points) {
  const out = [];
  let open = null;
  for (const p of points || []) {
    if (p.in_shadow && !open) open = p.t;
    if (!p.in_shadow && open) {
      out.push([open, p.t]);
      open = null;
    }
  }
  if (open && points?.length) out.push([open, points[points.length - 1].t]);
  return out;
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

function liveSourceStatus(dq) {
  const s = String(dq || "");
  if (/STALE/i.test(s)) return "stale";
  if (/ERROR|FAIL/i.test(s)) return "error";
  return "ok";
}

function isCelestrakSource(s) {
  return /celestrak|socrates/i.test(`${s?.name || ""} ${s?.url || ""}`);
}

function isMockMmodFact(w) {
  if (!w) return false;
  const blob = `${w.id || ""} ${w.title || ""} ${w.source || ""}`;
  if (/meteor|ариет|iau/i.test(blob)) return true;
  return /сближен|cdm|miss|tca|conjunction|space-track/i.test(blob);
}

function peakRelativeSpeed(payload) {
  const fromIv = payload?.critical_intervals?.[0]?.maximum_relative_speed_km_s;
  if (fromIv != null && Number.isFinite(Number(fromIv))) return Number(fromIv);
  const speeds = (payload?.samples || [])
    .map((s) => s.relative_speed_km_s)
    .filter((n) => n != null && Number.isFinite(Number(n)));
  return speeds.length ? Math.max(...speeds) : null;
}

function patchLiveOrbitSources(metadata, payload) {
  if (!metadata) return metadata;
  const fetched = payload?.source?.retrieved_at || payload?.data_quality?.calculated_at;
  if (!fetched && !payload?.source?.name) return metadata;
  const age = fetched ? ageLabel(fetched) : null;
  const status = liveSourceStatus(payload?.data_quality?.status);
  const sources = (metadata?.sources || []).map((s) => {
    if (!isCelestrakSource(s)) return s;
    return {
      ...s,
      fetched_at: fetched || s.fetched_at,
      age: age || s.age,
      status,
    };
  });
  return { ...metadata, sources };
}

function swReasonBit(data) {
  const av = data?.risk_sw?.availability;
  if (av && av !== "ok") return null;
  const r = Number(data?.risk_sw?.risk_sw) || 0;
  if (r >= 0.6) return "Высокий риск радиации";
  if (r >= 0.35) return "Повышенный риск радиации";
  return "Низкий риск радиации";
}

function mmodReasonBit({ hasDist, miss, tca, name, critical }) {
  if (!hasDist || miss == null) return "MMOD не прогнозируется";
  const who = name || "объект";
  const tcaText = tca ? `, TCA ${formatUtc(tca)}` : "";
  const crit = Number(miss) < Number(critical) ? " — ближе порога" : "";
  return `ближайший объект ${who}: miss ${formatNumber(miss, 2)} км${tcaText}${crit}`;
}

function liveConjunctionWarning(payload, data, { miss, tca, critical }) {
  const obj = payload?.summary?.nearest_object;
  const name = obj?.name || (obj?.norad_id != null ? `NORAD ${obj.norad_id}` : "объект");
  const start = data.request?.start;
  const duration = Number(data.request?.duration) || 0;
  const end = start && duration ? addHoursIso(start, duration) : payload?.request?.end_time;
  const interval = payload?.critical_intervals?.[0];
  const published = payload?.source?.retrieved_at || payload?.data_quality?.calculated_at || tca;
  let intersects = false;
  if (tca && start && end) {
    const t = Date.parse(tca);
    intersects = t >= Date.parse(start) && t <= Date.parse(end);
  }
  return {
    id: "w-mmod-conj-live",
    type: "computed",
    mechanism: "mmod",
    title: `Сближение: ${name}, miss ${formatNumber(miss, 2)} км`,
    source: payload?.source?.name || "CelesTrak / CosmoHACK Orbit API",
    source_url: "https://celestrak.org/NORAD/elements/",
    published_at: published,
    event_time: tca,
    period: interval
      ? { start: interval.start_time || interval.start, end: interval.end_time || interval.end }
      : tca
        ? { start: tca, end: tca }
        : null,
    value: miss,
    unit: "km",
    impact:
      Number(miss) < Number(critical)
        ? `Минимальное расстояние меньше порога ${formatNumber(critical, 1)} км.`
        : `Минимальное расстояние ${formatNumber(miss, 2)} км при пороге ${formatNumber(critical, 1)} км.`,
    rule: "POST /conjunctions/distances; риск — сигмоида вокруг порога сближения.",
    limitations: "Шаг выборки 60 с; TCA берётся из сводки API, не из 60-секундных отсчётов.",
    confidence: "medium",
    intersects_window: intersects,
  };
}

function applyMmodNarrative(data, payload, ctx) {
  const warnings = (data.risk_mmod?.warnings || []).filter((w) => !isMockMmodFact(w));
  if (ctx.hasDist && ctx.miss != null) warnings.unshift(liveConjunctionWarning(payload, data, ctx));

  const obj = payload?.summary?.nearest_object;
  const mmodBit = mmodReasonBit({
    ...ctx,
    name: obj?.name || (obj?.norad_id != null ? `NORAD ${obj.norad_id}` : null),
  });
  const mmodSentence = `${mmodBit.charAt(0).toUpperCase()}${mmodBit.slice(1)}.`;
  const swBit = swReasonBit(data);
  const prev = data.recommendation?.reason || "";
  const rec = data.recommendation?.window;
  const recRow = rec ? data.windows?.find((w) => w.start === rec.start && w.end === rec.end) : null;
  const canAvoid = (data.windows || []).some((w) => !w.contains_conjunction);
  const avoided = Boolean(recRow && !recRow.contains_conjunction);
  let reason = swBit ? `${swBit}. ${mmodSentence}` : `${prev} ${mmodSentence}`.trim();
  if (ctx.hasDist && ctx.miss != null && rec) {
    reason += avoided
      ? " Рекомендованное окно обходит это сближение."
      : canAvoid
        ? ""
        : " Безопасного слота такой длительности в периоде нет.";
  }
  const dq = payload?.data_quality?.status;
  let confidence_mmod = data.metadata?.confidence_mmod;
  if (dq === "NO_CANDIDATES" || !ctx.hasDist) confidence_mmod = "низкая";
  else if (ctx.hasDist) confidence_mmod = "высокая";

  const later = (data.verification?.later_observations || []).filter((w) => !isMockMmodFact(w));
  const metadata = patchLiveOrbitSources(
    { ...data.metadata, confidence_mmod },
    payload,
  );

  return {
    ...data,
    metadata,
    risk_mmod: {
      ...data.risk_mmod,
      warnings,
      components: {
        ...(data.risk_mmod?.components || {}),
        meteor: null,
      },
      metrics: {
        ...data.risk_mmod.metrics,
        pc: null,
        relative_speed_km_s: ctx.relativeSpeed ?? data.risk_mmod?.metrics?.relative_speed_km_s ?? null,
      },
    },
    recommendation: {
      ...data.recommendation,
      reason,
      limitations: `Расстояние MMOD — CosmoHACK Orbit API, порог ${formatNumber(ctx.critical, 1)} км.`,
    },
    verification: data.verification ? { ...data.verification, later_observations: later } : data.verification,
  };
}

export function applyOrbitPositions(data, payload) {
  const { iss_trajectory, debris, closest_approach } = positionsToTrajectories(payload);
  const start = payload?.request?.start_time || data.request?.start;
  const series = debrisDistanceSeries(payload, start);
  const peak = series.reduce((m, p) => Math.max(m, p.risk || 0), 0);
  const epoch = payload?.data_quality?.elements_epoch || data.orbit?.tle_epoch;
  const ageHours =
    epoch && start ? Number(((Date.parse(start) - Date.parse(epoch)) / 3600_000).toFixed(2)) : data.orbit?.tle_age_hours;
  return {
    ...data,
    metadata: patchLiveOrbitSources(data.metadata, payload),
    orbit: {
      ...data.orbit,
      tle_source: payload?.source?.name || data.orbit?.tle_source,
      tle_epoch: epoch,
      tle_age_hours: ageHours,
      geometry_kind: "operational",
      coordinate_frame: payload?.coordinate_frame || "TEME",
      data_quality: payload?.data_quality || null,
      iss_trajectory,
      shadow_intervals: shadowIntervalsFromPoints(iss_trajectory),
      debris,
      closest_approach,
    },
    risk_mmod: {
      ...data.risk_mmod,
      time_series: series,
      risk_mmod: round(peak, 3),
      availability: series.some((p) => p.distance_km != null) ? "ok" : data.risk_mmod?.availability,
      metrics: {
        ...data.risk_mmod?.metrics,
        miss_distance: closest_approach?.distance_km ?? data.risk_mmod?.metrics?.miss_distance,
        tca: closest_approach?.t ?? data.risk_mmod?.metrics?.tca,
      },
      components: {
        ...data.risk_mmod?.components,
        conjunction: round(peak, 3),
      },
    },
  };
}

function thinSamples(samples, maxN = 900) {
  if (!samples?.length || samples.length <= maxN) return samples || [];
  const step = Math.ceil(samples.length / maxN);
  const out = [];
  for (let i = 0; i < samples.length; i += step) out.push(samples[i]);
  const last = samples[samples.length - 1];
  if (out[out.length - 1] !== last) out.push(last);
  return out;
}

export function conjunctionDistanceSeries(payload, startIso) {
  const start = Date.parse(startIso);
  const critical = payload?.request?.critical_distance_km ?? CRITICAL_DISTANCE_KM;
  const rows = [];
  for (const sample of thinSamples(payload?.samples || [])) {
    const t = Number.isFinite(start) ? (Date.parse(sample.timestamp) - start) / 3600_000 : 0;
    const d = sample.distance_km;
    rows.push({
      t: round(t, 4),
      distance_km: d != null && Number.isFinite(Number(d)) ? round(Number(d), 3) : null,
      is_critical: sample.is_critical ?? null,
      relative_speed_km_s: sample.relative_speed_km_s ?? null,
    });
  }
  const tca = payload?.summary?.tca;
  const minD = payload?.summary?.minimum_distance_km;
  if (tca && minD != null && Number.isFinite(start)) {
    const t = (Date.parse(tca) - start) / 3600_000;
    if (Number.isFinite(t) && !rows.some((r) => Math.abs(r.t - t) < 1 / 3600)) {
      rows.push({
        t: round(t, 4),
        distance_km: round(Number(minD), 3),
        is_critical: Number(minD) < Number(critical),
        relative_speed_km_s: payload?.critical_intervals?.[0]?.maximum_relative_speed_km_s ?? null,
      });
      rows.sort((a, b) => a.t - b.t);
    }
  }
  return rows.map((r) => ({ ...r, risk: distanceToMmodRisk(r.distance_km, critical) }));
}

function hoursFromIso(iso, startIso) {
  return (Date.parse(iso) - Date.parse(startIso)) / 3600_000;
}

function seriesStatsInRange(series, t0, t1) {
  let maxRisk = 0;
  let overlap = 0;
  for (let i = 0; i < series.length; i += 1) {
    const p = series[i];
    if (p.t < t0 - 1e-9 || p.t > t1 + 1e-9) continue;
    maxRisk = Math.max(maxRisk, p.risk || 0);
    const next = series[i + 1];
    const dt = next && next.t <= t1 + 1e-9 ? Math.max(0, next.t - p.t) : 0;
    if ((p.risk || 0) >= 0.3) overlap += dt;
  }
  return { maxRisk: round(maxRisk, 3), overlap: round(overlap, 2) };
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

function rescoreWindows(data, series) {
  const startIso = data.request?.start;
  if (!startIso || !Array.isArray(data.windows)) return data;
  const windows = data.windows.map((w) => {
    const t0 = hoursFromIso(w.start, startIso);
    const t1 = hoursFromIso(w.end, startIso);
    const { maxRisk, overlap } = seriesStatsInRange(series, t0, t1);
    const rsw = w.risk_sw ?? 0;
    const completeness = w.completeness ?? 1;
    const contains_conjunction = windowHasConjunction(w, data);
    const score = round(
      clamp01(1 - 0.55 * rsw - 0.45 * maxRisk - 0.04 * (w.overlap_sw || 0) - 0.03 * overlap) * completeness,
      3,
    );
    return {
      ...w,
      risk_mmod: maxRisk,
      overlap_mmod: overlap,
      contains_conjunction,
      score,
      requires_check: completeness < 0.9 || rsw > 0.4 || maxRisk > 0.35 || contains_conjunction,
    };
  });
  windows.sort((a, b) => {
    if (Boolean(a.contains_conjunction) !== Boolean(b.contains_conjunction)) {
      return a.contains_conjunction ? 1 : -1;
    }
    return (b.score ?? 0) - (a.score ?? 0);
  });
  const winner = windows[0] || null;
  const sameClass = windows.filter((w) => Boolean(w.contains_conjunction) === Boolean(winner?.contains_conjunction));
  const tie = Boolean(winner && sameClass[1] && sameClass[1].score === winner.score);
  return {
    ...data,
    windows,
    recommendation: {
      ...data.recommendation,
      window: winner ? { start: winner.start, end: winner.end } : data.recommendation?.window,
      score: winner?.score ?? data.recommendation?.score,
      tie,
    },
  };
}

export function applyConjunctionDistances(data, payload) {
  if (!payload) return data;
  const start = payload?.request?.start_time || data.request?.start;
  const series = conjunctionDistanceSeries(payload, start);
  const hasDist = series.some((p) => p.distance_km != null);
  const miss = payload?.summary?.minimum_distance_km ?? null;
  const tca = payload?.summary?.tca ?? null;
  const relativeSpeed = peakRelativeSpeed(payload);
  const critical = payload?.request?.critical_distance_km ?? CRITICAL_DISTANCE_KM;
  const conjunction = hasDist
    ? distanceToMmodRisk(
        miss ?? Math.min(...series.map((p) => p.distance_km).filter((d) => d != null)),
        critical,
      )
    : 0;
  const next = {
    ...data,
    orbit: {
      ...data.orbit,
      conjunction: {
        summary: payload.summary || null,
        critical_intervals: payload.critical_intervals || [],
        data_quality: payload.data_quality || null,
      },
      debris: (data.orbit?.debris || []).map((track) => {
        const norad = payload.summary?.nearest_object?.norad_id;
        if (norad != null && track.norad_id === norad && miss != null) {
          return { ...track, miss_km: miss };
        }
        return track;
      }),
    },
    risk_mmod: {
      ...data.risk_mmod,
      time_series: series,
      risk_mmod: round(conjunction, 3),
      availability: hasDist ? "ok" : "insufficient_data",
      metrics: {
        ...data.risk_mmod?.metrics,
        miss_distance: miss,
        tca,
        pc: null,
        relative_speed_km_s: relativeSpeed,
      },
      components: {
        ...data.risk_mmod?.components,
        conjunction: round(conjunction, 3),
      },
    },
  };
  const scored = hasDist ? rescoreWindows(next, series) : next;
  return applyMmodNarrative(scored, payload, { hasDist, miss, tca, critical, relativeSpeed });
}
