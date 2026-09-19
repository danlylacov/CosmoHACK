import { describe, expect, it } from "vitest";
import {
  applyConjunctionDistances,
  applyOrbitPositions,
  conjunctionDistanceSeries,
  debrisDistanceSeries,
  debrisMissKm,
  debrisTrackId,
  distanceToMmodRisk,
  formatChartNow,
  pointAtTime,
  positionsToTrajectories,
  playbackStopHours,
  stepPlayback,
  subsolarPoint,
  sunlitAmount,
  syncDebrisVisible,
  toOrbitWindow,
} from "../orbits.js";

const sample = {
  request: { start_time: "2026-09-19T00:00:00Z", end_time: "2026-09-19T00:02:00Z", step_seconds: 60 },
  source: { name: "CelesTrak SOCRATES Plus / CelesTrak GP", retrieved_at: "2026-09-19T10:50:31Z" },
  coordinate_frame: "TEME",
  samples: [
    {
      timestamp: "2026-09-19T00:00:00Z",
      iss: {
        norad_id: 25544,
        position_km: { x: 2672.964, y: -3498.367, z: 5167.179 },
      },
      screened_objects: [
        {
          norad_id: 100057,
          name: "SOYUZ-MS 29",
          position_km: { x: 2672.964, y: -3498.367, z: 5167.179 },
        },
        {
          norad_id: 60488,
          name: "FLOCK 4BE-31",
          position_km: { x: 5411.507, y: -521.582, z: -4094.625 },
        },
      ],
    },
    {
      timestamp: "2026-09-19T00:01:00Z",
      iss: {
        norad_id: 25544,
        position_km: { x: 3077.753, y: -3303.419, z: 5069.322 },
      },
      screened_objects: [
        {
          norad_id: 60488,
          name: "FLOCK 4BE-31",
          position_km: { x: 5663.087, y: -619.849, z: -3723.634 },
        },
      ],
    },
  ],
  data_quality: { status: "COMPLETE", elements_epoch: "2026-09-19T04:11:54Z", calculated_at: "2026-09-19T11:19:52Z" },
};

describe("subsolarPoint", () => {
  it("moves 180° of longitude in 12 hours", () => {
    const a = subsolarPoint("2026-09-19T00:00:00Z").lon;
    const b = subsolarPoint("2026-09-19T12:00:00Z").lon;
    let d = Math.abs(b - a) % 360;
    if (d > 180) d = 360 - d;
    expect(d).toBeCloseTo(180, 0);
  });

  it("fades through twilight instead of a hard night cut", () => {
    const sun = subsolarPoint("2026-09-19T12:00:00Z");
    const iso = "2026-09-19T12:00:00Z";
    expect(sunlitAmount(sun.lat, sun.lon, iso)).toBeCloseTo(1, 2);
    expect(sunlitAmount(sun.lat, sun.lon + 180, iso)).toBeCloseTo(0, 2);
    const sunset = sunlitAmount(sun.lat, sun.lon + 90, iso);
    expect(sunset).toBeGreaterThan(0.2);
    expect(sunset).toBeLessThan(1);
  });
});

describe("toOrbitWindow", () => {
  it("builds start/end from form start and period", () => {
    expect(toOrbitWindow({ start: "2026-09-19T00:00:00Z", duration: 1, period: 4 })).toEqual({
      start_time: "2026-09-19T00:00:00Z",
      end_time: "2026-09-19T04:00:00Z",
    });
  });
});

describe("positionsToTrajectories", () => {
  it("maps ISS and skips docked Soyuz", () => {
    const { iss_trajectory, debris, closest_approach } = positionsToTrajectories(sample);
    expect(iss_trajectory).toHaveLength(2);
    expect(iss_trajectory[0].alt_km).toBeGreaterThan(300);
    expect(debris.map((d) => d.norad_id)).toEqual([60488]);
    expect(debris[0].name).toBe("FLOCK 4BE-31");
    expect(closest_approach.norad_id).toBe(60488);
    expect(closest_approach.distance_km).toBeGreaterThan(0);
  });
});

describe("debrisTrackId", () => {
  it("prefers norad id and keeps previous visibility", () => {
    const { debris } = positionsToTrajectories(sample);
    expect(debrisTrackId(debris[0], 0)).toBe("60488");
    expect(debrisMissKm(debris[0])).toBeGreaterThan(0);
    expect(syncDebrisVisible(debris, { 60488: false })).toEqual({ 60488: false });
    expect(syncDebrisVisible(debris, {})).toEqual({ 60488: true });
  });
});

describe("debrisDistanceSeries", () => {
  it("maps closer debris to higher chart risk", () => {
    const series = debrisDistanceSeries(sample, sample.request.start_time);
    expect(series).toHaveLength(2);
    expect(series[0].distance_km).not.toBe(series[1].distance_km);
    const closer = series[0].distance_km < series[1].distance_km ? series[0] : series[1];
    const farther = series[0].distance_km > series[1].distance_km ? series[0] : series[1];
    expect(closer.risk).toBe(1);
    expect(farther.risk).toBeCloseTo(0.08, 2);
    expect(formatChartNow("mmod", series, closer.t)).toMatch(/км/);
  });
});

describe("pointAtTime", () => {
  it("moves along the debris track between samples", () => {
    const { debris } = positionsToTrajectories(sample);
    const pts = debris[0].points;
    const t0 = Date.parse(pts[0].t);
    const t1 = Date.parse(pts[1].t);
    const mid = pointAtTime(pts, (t0 + t1) / 2);
    expect(mid.lat).not.toBe(pts[0].lat);
    expect(mid.lat).not.toBe(pts[1].lat);
  });
});

describe("applyOrbitPositions", () => {
  it("replaces mock trajectory with live samples", () => {
    const data = applyOrbitPositions(
      {
        request: { start: "2026-09-19T00:00:00Z" },
        orbit: { tle_source: "mock", iss_trajectory: [] },
        risk_mmod: { time_series: [{ t: 7.2, risk: 0.9 }], metrics: {}, components: {} },
      },
      sample,
    );
    expect(data.orbit.iss_trajectory).toHaveLength(2);
    expect(data.orbit.tle_source).toMatch(/CelesTrak/);
    expect(data.orbit.debris[0].norad_id).toBe(60488);
    expect(data.risk_mmod.time_series).toHaveLength(2);
    expect(data.risk_mmod.time_series[0].risk).not.toBe(data.risk_mmod.time_series[1].risk);
    expect(data.risk_mmod.metrics.miss_distance).toBe(data.orbit.closest_approach.distance_km);
  });

  it("keeps debris empty when SOCRATES has no candidates", () => {
    const empty = {
      ...sample,
      samples: sample.samples.map((s) => ({ ...s, screened_objects: [] })),
      data_quality: { ...sample.data_quality, status: "NO_CANDIDATES" },
    };
    const data = applyOrbitPositions(
      {
        request: { start: "2026-09-19T00:00:00Z" },
        orbit: {},
        risk_mmod: { time_series: [], metrics: {}, components: {} },
      },
      empty,
    );
    expect(data.orbit.debris).toEqual([]);
    expect(data.orbit.closest_approach).toBeNull();
    expect(data.risk_mmod.time_series.every((p) => p.distance_km == null)).toBe(true);
    expect(Array.isArray(data.orbit.shadow_intervals)).toBe(true);
  });
});

describe("distanceToMmodRisk", () => {
  it("is 0.5 at the 5 km threshold and higher when closer", () => {
    expect(distanceToMmodRisk(5)).toBeCloseTo(0.5, 2);
    expect(distanceToMmodRisk(3.072)).toBeGreaterThan(0.5);
    expect(distanceToMmodRisk(3.072)).toBeLessThan(1);
    expect(distanceToMmodRisk(1000)).toBeLessThan(0.05);
    expect(distanceToMmodRisk(null)).toBe(0);
  });
});

describe("applyConjunctionDistances", () => {
  const conj = {
    request: {
      start_time: "2026-09-20T00:00:00Z",
      end_time: "2026-09-20T02:00:00Z",
      step_seconds: 60,
      critical_distance_km: 5,
    },
    samples: [
      { timestamp: "2026-09-20T00:00:00Z", nearest_object: { norad_id: 40701, name: "GAOFEN-8" }, distance_km: 1393.153, relative_speed_km_s: 11, is_critical: false },
      { timestamp: "2026-09-20T01:00:00Z", nearest_object: { norad_id: 40701, name: "GAOFEN-8" }, distance_km: 400, relative_speed_km_s: 10, is_critical: false },
    ],
    summary: {
      minimum_distance_km: 3.072,
      tca: "2026-09-20T01:30:00Z",
      nearest_object: { norad_id: 40701, name: "GAOFEN-8" },
      critical_duration_seconds: 1,
    },
    critical_intervals: [],
    data_quality: { status: "COMPLETE" },
    source: { name: "CelesTrak SOCRATES Plus / CelesTrak GP", retrieved_at: "2026-09-20T00:10:00Z" },
  };

  it("drives the MMOD chart and Risk MMOD from miss distance", () => {
    const series = conjunctionDistanceSeries(conj, conj.request.start_time);
    expect(series.some((p) => p.distance_km === 3.072)).toBe(true);
    const data = applyConjunctionDistances(
      {
        request: { start: "2026-09-20T00:00:00Z", duration: 1, period: 2 },
        orbit: {},
        risk_sw: { risk_sw: 0.2, availability: "ok" },
        risk_mmod: {
          time_series: [{ t: 0, risk: 0.9 }],
          metrics: { miss_distance: 4.8, tca: "mock", pc: 0.000012 },
          components: { conjunction: 0.9, meteor: 0.12 },
          warnings: [
            { id: "w-mmod-conj", mechanism: "mmod", title: "Сближение: TCA 15:12 UTC, miss 4.8 км", source: "Space-Track CDM" },
            { id: "w-mmod-meteor", mechanism: "mmod", title: "Дневные Ариетиды: фоновый метеорный поток", source: "IAU Meteor Data Center" },
          ],
        },
        windows: [
          { start: "2026-09-20T00:00:00Z", end: "2026-09-20T01:00:00Z", score: 0.9, risk_sw: 0.2, risk_mmod: 0.9, overlap_sw: 0, completeness: 1 },
          { start: "2026-09-20T01:00:00Z", end: "2026-09-20T02:00:00Z", score: 0.2, risk_sw: 0.2, risk_mmod: 0.1, overlap_sw: 0, completeness: 1 },
        ],
        recommendation: { window: { start: "a", end: "b" }, score: 0.9, tie: false, reason: "Низкий риск радиации и отсутствие сближений MMOD" },
        metadata: { sources: [{ name: "CelesTrak TLE ISS (25544)", url: "https://celestrak.org/NORAD/elements/", fetched_at: "2024-06-15T07:30:00Z", age: "30 мин", status: "ok" }] },
      },
      conj,
    );
    expect(data.risk_mmod.metrics.miss_distance).toBe(3.072);
    expect(data.risk_mmod.metrics.tca).toBe("2026-09-20T01:30:00Z");
    expect(data.risk_mmod.risk_mmod).toBe(distanceToMmodRisk(3.072));
    expect(data.risk_mmod.components.conjunction).toBe(data.risk_mmod.risk_mmod);
    expect(data.risk_mmod.availability).toBe("ok");
    expect(data.risk_mmod.time_series.length).toBeGreaterThanOrEqual(3);
    expect(data.risk_mmod.metrics.pc).toBeNull();
    expect(data.risk_mmod.components.meteor).toBeNull();
    expect(data.risk_mmod.metrics.relative_speed_km_s).toBe(11);
    expect(data.risk_mmod.warnings[0].title).toMatch(/GAOFEN-8/);
    expect(data.risk_mmod.warnings.some((w) => /4\.8/.test(w.title || ""))).toBe(false);
    expect(data.recommendation.reason).toMatch(/GAOFEN-8/);
    expect(data.recommendation.reason).not.toMatch(/отсутствие сближений/);
    expect(data.risk_mmod.warnings.some((w) => w.id === "w-mmod-meteor")).toBe(false);
    expect(data.metadata.sources[0].fetched_at).toBe("2026-09-20T00:10:00Z");
    expect(data.recommendation.window.start).toBe("2026-09-20T00:00:00Z");
    expect(data.recommendation.reason).toMatch(/обходит/);
  });

  it("prefers a window that avoids TCA even if mock SW score is worse", () => {
    const data = applyConjunctionDistances(
      {
        request: { start: "2026-09-20T00:00:00Z", duration: 1, period: 2 },
        orbit: {},
        risk_sw: { risk_sw: 0.2, availability: "ok" },
        risk_mmod: { time_series: [], metrics: {}, components: {}, warnings: [] },
        windows: [
          { start: "2026-09-20T01:00:00Z", end: "2026-09-20T02:00:00Z", score: 0.99, risk_sw: 0.05, risk_mmod: 0.1, overlap_sw: 0, completeness: 1 },
          { start: "2026-09-20T00:00:00Z", end: "2026-09-20T01:00:00Z", score: 0.1, risk_sw: 0.8, risk_mmod: 0.1, overlap_sw: 0, completeness: 1 },
        ],
        recommendation: { window: { start: "2026-09-20T01:00:00Z", end: "2026-09-20T02:00:00Z" }, score: 0.99 },
      },
      conj,
    );
    expect(data.recommendation.window.start).toBe("2026-09-20T00:00:00Z");
    expect(data.windows[0].contains_conjunction).toBe(false);
    expect(data.windows[1].contains_conjunction).toBe(true);
  });

  it("marks MMOD as insufficient when SOCRATES has no candidates", () => {
    const data = applyConjunctionDistances(
      {
        request: { start: "2026-09-20T00:00:00Z" },
        orbit: {},
        risk_sw: { risk_sw: 0.12, availability: "ok" },
        risk_mmod: {
          time_series: [{ t: 0, risk: 0.9 }],
          metrics: { miss_distance: 4.8 },
          components: { conjunction: 0.9 },
          warnings: [{ id: "w-mmod-conj", mechanism: "mmod", title: "Сближение: TCA 15:12 UTC, miss 4.8 км", source: "Space-Track CDM" }],
        },
        recommendation: { reason: "Низкий риск радиации и отсутствие сближений MMOD" },
      },
      {
        request: { start_time: "2026-09-20T00:00:00Z", end_time: "2026-09-20T01:00:00Z", critical_distance_km: 5 },
        samples: [{ timestamp: "2026-09-20T00:00:00Z", nearest_object: null, distance_km: null, relative_speed_km_s: null, is_critical: null }],
        summary: { minimum_distance_km: null, tca: null, nearest_object: null, critical_duration_seconds: 0 },
        critical_intervals: [],
        data_quality: { status: "NO_CANDIDATES" },
      },
    );
    expect(data.risk_mmod.availability).toBe("insufficient_data");
    expect(data.risk_mmod.risk_mmod).toBe(0);
    expect(data.risk_mmod.metrics.miss_distance).toBeNull();
    expect(data.recommendation.reason).toMatch(/MMOD не прогнозируется/);
    expect(data.risk_mmod.warnings.every((w) => w.id !== "w-mmod-conj")).toBe(true);
  });
});

describe("playback stops", () => {
  const data = {
    request: { start: "2026-09-20T00:00:00Z", period: 12 },
    orbit: {
      conjunction: {
        summary: { tca: "2026-09-20T04:36:53Z" },
        critical_intervals: [{ start_time: "2026-09-20T08:00:00Z", tca: "2026-09-20T08:10:00Z" }],
      },
    },
    risk_mmod: { metrics: { tca: "2026-09-20T04:36:53Z" } },
  };

  it("collects TCA and interval stops inside the window", () => {
    const stops = playbackStopHours(data);
    expect(stops).toHaveLength(2);
    expect(stops[0]).toBeCloseTo(4 + 36 / 60 + 53 / 3600, 5);
    expect(stops[1]).toBeCloseTo(8 + 10 / 60, 5);
  });

  it("pauses exactly on the next stop and skips it after resume", () => {
    const stops = playbackStopHours(data);
    const hit = stepPlayback(4.5, 0.2, stops, 12);
    expect(hit.pause).toBe(true);
    expect(hit.reason).toBe("conjunction");
    expect(hit.t).toBeCloseTo(stops[0], 5);
    const after = stepPlayback(hit.t, 0.2, stops, 12);
    expect(after.pause).toBe(false);
    expect(after.t).toBeGreaterThan(hit.t);
  });

  it("stops at the end of the window", () => {
    const end = stepPlayback(11.9, 0.5, [], 12);
    expect(end).toEqual({ t: 12, pause: true, reason: "end" });
  });
});
