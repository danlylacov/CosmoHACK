import { describe, expect, it } from "vitest";
import { applyEvaWindows } from "../eva.js";
import { applyForecast } from "../forecast.js";
import { finalizeLive, patchLiveHealth, stripMockCatalog } from "../live.js";

const catalog = {
  request: { start: "2026-09-20T00:00:00Z", duration: 1, period: 2 },
  windows: [{ start: "mock-a", end: "mock-b", score: 0.99, risk_sw: 0.1, risk_mmod: 0.1 }],
  recommendation: { window: { start: "mock-a", end: "mock-b" }, reason: "мок", score: 0.99 },
  risk_sw: {
    availability: "ok",
    risk_sw: 0.4,
    components: { sep: 0.2, cme: 0.3, kp: 0.1, flare: 0.1 },
    metrics: { kp: 4, sep_10mev: 2, cme_speed: 680 },
    time_series: [{ t: 0, risk: 0.4 }],
    warnings: [
      { id: "w-sw-cme", title: "CME 680 km/s", source: "NASA DONKI / WSA-Enlil" },
      { id: "w-sw-kp", title: "Kp 4", source: "NOAA SWPC" },
    ],
  },
  risk_mmod: {
    availability: "ok",
    risk_mmod: 0.2,
    components: { conjunction: 0.2, meteor: 0.12 },
    metrics: { miss_distance: 4.8, pc: 0.000012, tca: "mock" },
    time_series: [{ t: 0, risk: 0.2, distance_km: 400 }],
    warnings: [{ id: "w-mmod-meteor", title: "Дневные Ариетиды", source: "IAU Meteor Data Center" }],
  },
  verification: {
    later_observations: [{ id: "verify-cme", title: "CME later", source: "NASA DONKI" }],
  },
  metadata: {
    completeness: 0.94,
    sources: [
      { name: "NOAA SWPC (Kp, GOES protons)", url: "https://services.swpc.noaa.gov/", status: "ok" },
      { name: "NASA DONKI (CME / WSA-Enlil)", url: "https://kauai.ccmc.gsfc.nasa.gov/DONKI/", status: "ok" },
      { name: "CelesTrak TLE ISS (25544)", url: "https://celestrak.org/NORAD/elements/", status: "ok" },
      { name: "Space-Track CDM", url: "https://www.space-track.org/", status: "stale" },
      { name: "IAU Meteor Data Center", url: "https://www.ta3.sk/IAUC22DB/MDC2007/", status: "ok" },
    ],
  },
};

const evaPayload = {
  windows: [
    {
      start: "2026-09-20T00:10:00Z",
      end: "2026-09-20T01:10:00Z",
      status: "SAFE",
      safety: 0.91,
      peak_danger: 0.09,
      data_coverage: 1,
      distance_min_km: 264,
      blocked: false,
    },
  ],
  data_quality: { status: "COMPLETE", calculated_at: "2026-09-20T00:01:00Z" },
};

const forecastPayload = {
  issued_at_utc: "2026-09-20T00:02:00Z",
  policy: { version: "space-weather-sgr-research-v1", block_s: 1, block_g: 1, block_r: 1 },
  windows: [
    {
      start_utc: "2026-09-20T00:00:00Z",
      end_utc: "2026-09-20T00:30:00Z",
      geomagnetic_kp_pred: 1.8,
      proton_flux_above_10_mev_max_pred: 0.3,
      proton_flux_above_50_mev_max_pred: 0.1,
      proton_flux_above_100_mev_max_pred: 0.05,
      solar_xray_flux_long_max_pred: 2e-7,
      s_level: 0,
      g_level: 0,
      r_level: 0,
      p_adverse: 0.00001,
      eva_allowed: true,
    },
    {
      start_utc: "2026-09-20T00:30:00Z",
      end_utc: "2026-09-20T02:00:00Z",
      geomagnetic_kp_pred: 1.9,
      proton_flux_above_10_mev_max_pred: 0.3,
      solar_xray_flux_long_max_pred: 2e-7,
      s_level: 0,
      g_level: 0,
      r_level: 0,
      p_adverse: 0.00002,
      eva_allowed: true,
    },
  ],
};

describe("stripMockCatalog", () => {
  it("drops NOAA/DONKI/CDM/meteors and nulls fields without an API", () => {
    const data = stripMockCatalog(catalog);
    expect(data.metadata.sources.map((s) => s.name)).toEqual(["CelesTrak TLE ISS (25544)"]);
    expect(data.risk_sw.warnings).toEqual([]);
    expect(data.risk_mmod.warnings).toEqual([]);
    expect(data.risk_sw.metrics.cme_speed).toBeNull();
    expect(data.risk_sw.components.cme).toBeNull();
    expect(data.risk_mmod.components.meteor).toBeNull();
    expect(data.risk_mmod.metrics.pc).toBeNull();
    expect(data.verification.later_observations).toEqual([]);
  });
});

describe("finalizeLive", () => {
  it("fills SW/EVA slots from live overlays and drops mock scores", () => {
    const data = finalizeLive(applyForecast(applyEvaWindows(catalog, evaPayload), forecastPayload));
    expect(data.windows[0].start).toBe("2026-09-20T00:10:00Z");
    expect(data.windows[0].eva_allowed).toBe(true);
    expect(data.risk_sw.metrics.kp).toBeCloseTo(1.9);
    expect(data.risk_sw.metrics.cme_speed).toBeNull();
    expect(data.recommendation.reason).toMatch(/Погода: выход разрешён/);
    expect(data.recommendation.limitations).toMatch(/CME и метеорные потоки/);
    expect(data.metadata.sources.some((s) => s.name === "CosmoHACK SW Forecast")).toBe(true);
    expect(data.metadata.sources.some((s) => /NOAA|DONKI|Space-Track|IAU/.test(s.name))).toBe(false);
  });

  it("builds equal-duration candidates when EVA is missing", () => {
    const data = finalizeLive(applyForecast(catalog, forecastPayload));
    expect(data.windows).toHaveLength(2);
    expect(data.windows.map((w) => w.start).sort()).toEqual([
      "2026-09-20T00:00:00Z",
      "2026-09-20T01:00:00Z",
    ]);
    expect(data.windows.every((w) => w.start !== "mock-a")).toBe(true);
    expect(data.windows.every((w) => w.eva_allowed === true)).toBe(true);
  });
});

describe("patchLiveHealth", () => {
  it("marks matching live sources from /health probes", () => {
    const data = patchLiveHealth(
      {
        metadata: {
          sources: [
            { name: "CelesTrak TLE ISS (25544)", url: "https://celestrak.org/", status: "ok" },
            { name: "CosmoHACK EVA API", url: "http://92.255.110.133:8001/docs", status: "ok" },
            { name: "CosmoHACK SW Forecast", url: "http://92.255.110.133:8002/docs", status: "ok" },
          ],
        },
      },
      {
        orbit: { ok: true },
        eva: { ok: false },
        forecast: { ok: true },
      },
    );
    expect(data.metadata.sources[0].status).toBe("ok");
    expect(data.metadata.sources[1].status).toBe("error");
    expect(data.metadata.sources[2].status).toBe("ok");
  });
});
