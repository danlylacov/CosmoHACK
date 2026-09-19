import { describe, expect, it } from "vitest";
import {
  applyForecast,
  mergeEvaAllowedBands,
  seriesFromForecast,
  slotEvaAllowed,
  toForecastRequest,
  windowSwRisk,
} from "../forecast.js";
import { overlayWindows } from "../eva.js";

const payload = {
  model_type: "sepnet_sgr",
  forecast_origin_utc: "2026-09-18T00:00:00+00:00",
  issued_at_utc: "2026-09-19T19:27:54.356950+00:00",
  policy: { version: "space-weather-sgr-research-v1", block_s: 1, block_g: 1, block_r: 1 },
  decision_basis: "point_forecast_s_g_r_levels",
  windows: [
    {
      start_utc: "2026-09-18T00:00:00+00:00",
      end_utc: "2026-09-18T00:30:00+00:00",
      geomagnetic_kp_pred: 1.8,
      proton_flux_above_10_mev_max_pred: 0.33,
      solar_xray_flux_long_max_pred: 2.2e-7,
      s_level: 0,
      g_level: 0,
      r_level: 0,
      p_adverse: 0.00001,
      eva_allowed: true,
      reason: ["scales_below_policy_thresholds"],
    },
    {
      start_utc: "2026-09-18T00:30:00+00:00",
      end_utc: "2026-09-18T01:00:00+00:00",
      geomagnetic_kp_pred: 1.8,
      proton_flux_above_10_mev_max_pred: 0.32,
      solar_xray_flux_long_max_pred: 2e-7,
      s_level: 0,
      g_level: 0,
      r_level: 0,
      p_adverse: 0.00002,
      eva_allowed: true,
      reason: ["scales_below_policy_thresholds"],
    },
    {
      start_utc: "2026-09-18T01:00:00+00:00",
      end_utc: "2026-09-18T01:30:00+00:00",
      geomagnetic_kp_pred: 5.2,
      proton_flux_above_10_mev_max_pred: 12,
      solar_xray_flux_long_max_pred: 2e-5,
      s_level: 1,
      g_level: 1,
      r_level: 1,
      p_adverse: 0.8,
      eva_allowed: false,
      reason: ["s_g_r_at_or_above_block"],
    },
  ],
};

const base = {
  request: { start: "2026-09-18T00:00:00Z", duration: 1, period: 2 },
  recommendation: { window: { start: "mock-a", end: "mock-b" }, reason: "мок" },
  risk_sw: {
    risk_sw: 0.4,
    availability: "ok",
    components: { sep: 0.4, cme: 0.3, kp: 0.2, flare: 0.1 },
    metrics: { kp: 4, sep_10mev: 2, cme_speed: 500 },
    time_series: [{ t: 0, risk: 0.4 }],
    warnings: [{ id: "mock-cme" }],
  },
  metadata: { sources: [] },
};

describe("toForecastRequest", () => {
  it("sends origin from the query start", () => {
    expect(toForecastRequest({ start: "2026-09-18T00:00:00Z" })).toEqual({
      origin: "2026-09-18T00:00:00Z",
    });
  });

  it("adds cutoff only for replay", () => {
    const req = toForecastRequest({
      start: "2026-09-18T00:00:00Z",
      historical_intent: "replay",
      cutoff_time: "2026-09-18T00:00:00Z",
    });
    expect(req.cutoff).toBe("2026-09-18T00:00:00Z");
  });
});

describe("mergeEvaAllowedBands", () => {
  it("merges consecutive allowed slots and keeps a blocked slot separate", () => {
    const bands = mergeEvaAllowedBands(payload.windows.map((w) => ({
      start: w.start_utc,
      end: w.end_utc,
      eva_allowed: w.eva_allowed,
    })));
    expect(bands).toHaveLength(2);
    expect(bands[0].eva_allowed).toBe(true);
    expect(bands[0].end).toBe("2026-09-18T01:00:00+00:00");
    expect(bands[1].eva_allowed).toBe(false);
    expect(bands[1].blocked).toBe(true);
  });
});

describe("applyForecast", () => {
  it("overlays eva_allowed, replaces SW series, keeps EVA recommendation", () => {
    const data = applyForecast(base, payload);
    expect(data.recommendation.window.start).toBe("mock-a");
    expect(data.forecast.available).toBe(true);
    expect(data.forecast.overlay).toHaveLength(2);
    expect(data.forecast.eva_allowed_all).toBe(false);
    expect(data.risk_sw.metrics.kp).toBeCloseTo(5.2);
    expect(data.risk_sw.metrics.sep_10mev).toBe(12);
    expect(data.risk_sw.metrics.cme_speed).toBeNull();
    expect(data.risk_sw.components.cme).toBeNull();
    expect(data.risk_sw.metrics.xray_wm2).toBeCloseTo(2e-5);
    expect(data.risk_sw.metrics.p_adverse).toBe(0.8);
    expect(data.risk_sw.time_series.some((p) => p.eva_allowed === false)).toBe(true);
    expect(data.risk_sw.warnings.some((w) => w.id === "w-sw-forecast-policy")).toBe(true);
    expect(overlayWindows(data)[0].eva_allowed).toBe(true);
    expect(overlayWindows(data).some((w) => w.blocked)).toBe(true);
    expect(data.metadata.sources.some((s) => s.name === "CosmoHACK SW Forecast")).toBe(true);
  });

  it("keeps API peaks when the forecast origin misses the EVA horizon", () => {
    const data = applyForecast(
      { ...base, request: { start: "2026-09-20T19:00:00Z", duration: 6, period: 12 } },
      payload,
    );
    expect(data.forecast.available).toBe(true);
    expect(data.forecast.covers_horizon).toBe(false);
    expect(data.forecast.overlay).toEqual([]);
    expect(data.risk_sw.availability).toBe("insufficient_data");
    expect(data.risk_sw.metrics.kp).toBeCloseTo(5.2);
    expect(data.risk_sw.metrics.cme_speed).toBeNull();
    expect(data.risk_sw.warnings.some((w) => w.id === "w-sw-forecast-coverage")).toBe(true);
  });

  it("does not keep mock SW when forecast errors", () => {
    const data = applyForecast(base, null, { message: "SW down" });
    expect(data.risk_sw.availability).toBe("insufficient_data");
    expect(data.risk_sw.time_series).toEqual([]);
    expect(data.risk_sw.metrics.cme_speed).toBeNull();
    expect(data.risk_sw.warnings.some((w) => w.id === "w-sw-forecast-error")).toBe(true);
    expect(data.forecast.available).toBe(false);
    expect(data.forecast.error).toBe("SW down");
    expect(overlayWindows(data)[0].start).toBe("mock-a");
  });
});

describe("slotEvaAllowed", () => {
  it("is false when the recommended slot overlaps a blocked window", () => {
    const data = applyForecast(base, payload);
    expect(slotEvaAllowed(data, { start: "2026-09-18T00:10:00Z", end: "2026-09-18T00:40:00Z" })).toBe(true);
    expect(slotEvaAllowed(data, { start: "2026-09-18T00:50:00Z", end: "2026-09-18T01:20:00Z" })).toBe(false);
  });
});

describe("windowSwRisk", () => {
  it("lifts blocked slots to at least 0.65", () => {
    expect(windowSwRisk({ eva_allowed: true, geomagnetic_kp_pred: 1.8 })).toBeLessThan(0.35);
    expect(windowSwRisk({ eva_allowed: false, geomagnetic_kp_pred: 1.8 })).toBeGreaterThanOrEqual(0.65);
  });
});

describe("seriesFromForecast", () => {
  it("clips to the search period", () => {
    const series = seriesFromForecast(
      payload.windows.map((w) => ({
        start: w.start_utc,
        end: w.end_utc,
        eva_allowed: w.eva_allowed,
        geomagnetic_kp_pred: w.geomagnetic_kp_pred,
        proton_flux_above_10_mev_max_pred: w.proton_flux_above_10_mev_max_pred,
        solar_xray_flux_long_max_pred: w.solar_xray_flux_long_max_pred,
        s_level: w.s_level,
        g_level: w.g_level,
        r_level: w.r_level,
        p_adverse: w.p_adverse,
      })),
      "2026-09-18T00:00:00Z",
      2,
    );
    expect(series.every((p) => p.t <= 2)).toBe(true);
    expect(series.some((p) => p.eva_allowed === false)).toBe(true);
  });
});
