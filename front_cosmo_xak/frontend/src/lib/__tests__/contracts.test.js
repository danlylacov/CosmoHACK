import { describe, expect, it } from "vitest";
import { validateQuery, defaultQuery, toAnalyzeRequest } from "../validate.js";
import { dataReliable, overallStatus, riskTone, briefRecommendation } from "../risk.js";
import { describePlanChange } from "../plan.js";
import { resolveWarningUrl, applyControlsToRequest } from "../sources.js";
import { dateTimeToIso, formatUtc } from "../format.js";

describe("validateQuery", () => {
  it("accepts default historical replay", () => {
    expect(validateQuery(defaultQuery("historical"))).toEqual({});
  });

  it("rejects historical outside 2024-05-01..06-30", () => {
    const q = { ...defaultQuery("historical"), start: "2023-01-01T00:00:00Z", cutoff_time: "2023-01-01T00:00:00Z" };
    expect(validateQuery(q).start).toMatch(/01\.05\.2024/);
  });

  it("requires cutoff for replay", () => {
    const q = { ...defaultQuery("historical"), cutoff_time: null };
    expect(validateQuery(q).cutoff_time).toBeTruthy();
  });

  it("does not require cutoff for archive review", () => {
    const q = { ...defaultQuery("historical"), historical_intent: "review", cutoff_time: null };
    expect(validateQuery(q).cutoff_time).toBeUndefined();
  });

  it("rejects critical distance outside 0.1–10000 km", () => {
    expect(validateQuery({ ...defaultQuery("current"), critical_distance_km: 0 }).critical_distance_km).toBeTruthy();
    expect(validateQuery({ ...defaultQuery("current"), critical_distance_km: 3 })).toEqual({});
  });
});

describe("toAnalyzeRequest", () => {
  it("nulls historical fields in current mode", () => {
    const req = toAnalyzeRequest(defaultQuery("current"));
    expect(req.historical_intent).toBeNull();
    expect(req.cutoff_time).toBeNull();
  });
});

describe("risk presentation", () => {
  it("never returns ok when status is partial", () => {
    const data = {
      status: "partial",
      recommendation: { window: { start: "a", end: "b" } },
      risk_sw: { risk_sw: 0.1, availability: "ok" },
      risk_mmod: { risk_mmod: 0.1, availability: "ok" },
      metadata: { completeness: 1, sources: [{ status: "ok", enabled: true }] },
    };
    expect(dataReliable(data)).toBe(false);
    expect(overallStatus(data)).toBe("warn");
  });

  it("treats disabled mechanism as unknown, not green", () => {
    expect(riskTone(0.05, "disabled")).toBe("unknown");
  });

  it("maps Risk SW: below 0.35 safe, above 0.6 forbidden, middle attention", () => {
    expect(riskTone(0.34)).toBe("ok");
    expect(riskTone(0.35)).toBe("warn");
    expect(riskTone(0.6)).toBe("warn");
    expect(riskTone(0.61)).toBe("danger");
  });
});

describe("briefRecommendation", () => {
  it("does not call EVA SAFE a green recommendation when SW is missing", () => {
    const brief = briefRecommendation({
      eva: {
        available: true,
        windows: [{ start: "a", end: "b", status: "SAFE", blocked: false, distance_min_km: 78.8 }],
      },
      recommendation: {
        window: { start: "a", end: "b" },
        reason: "Прогноз есть, но не покрывает выбранный горизонт ВКД. ближайший объект GAOFEN-8: miss 5,40 км",
      },
      windows: [{ start: "a", end: "b", risk_sw: 0, risk_mmod: 0.46, eva_allowed: null, distance_min_km: 78.8 }],
      risk_sw: { availability: "insufficient_data", risk_sw: null },
      risk_mmod: { availability: "ok", risk_mmod: 0.46, metrics: { miss_distance: 5.4 } },
      forecast: { available: true, covers_horizon: false },
      metadata: { completeness: 1, sources: [{ status: "ok" }] },
    });
    expect(brief.status).toBe("warn");
    expect(brief.title).toMatch(/Внимание/);
    expect(brief.window.start).toBe("a");
    expect(brief.reason).toMatch(/горизонт/);
    expect(brief.caveat).toMatch(/По погоде/);
  });

  it("forbids exit when Risk SW is above 0.6 or eva_allowed is false", () => {
    const highSw = briefRecommendation({
      recommendation: { window: { start: "a", end: "b" }, reason: "Погода: выход запрещён на части горизонта" },
      windows: [{ start: "a", end: "b", risk_sw: 0.72, risk_mmod: 0.1 }],
      risk_sw: { availability: "ok", risk_sw: 0.72 },
      risk_mmod: { availability: "ok", risk_mmod: 0.1 },
      forecast: { available: true, covers_horizon: true, windows: [{ start: "a", end: "b", eva_allowed: true }] },
      metadata: { completeness: 1, sources: [{ status: "ok" }] },
    });
    expect(highSw.status).toBe("danger");
    expect(highSw.title).toMatch(/запрещён/);

    const blocked = briefRecommendation({
      recommendation: { window: { start: "a", end: "b" }, reason: "Погода: выход запрещён на части горизонта" },
      windows: [{ start: "a", end: "b", risk_sw: 0.1, risk_mmod: 0.1, eva_allowed: false }],
      risk_sw: { availability: "ok", risk_sw: 0.1 },
      risk_mmod: { availability: "ok", risk_mmod: 0.1 },
      forecast: {
        available: true,
        covers_horizon: true,
        windows: [{ start: "a", end: "b", eva_allowed: false }],
      },
      metadata: { completeness: 1, sources: [{ status: "ok" }] },
    });
    expect(blocked.status).toBe("danger");
  });

  it("recommends safe exit when SW and MMOD are below 0.35", () => {
    const brief = briefRecommendation({
      recommendation: {
        window: { start: "a", end: "b" },
        reason: "Погода: выход разрешён (S/G/R ниже порога политики). ближайший объект GAOFEN-8: miss 80,00 км",
      },
      windows: [{ start: "a", end: "b", risk_sw: 0.12, risk_mmod: 0.2, eva_allowed: true }],
      risk_sw: { availability: "ok", risk_sw: 0.12 },
      risk_mmod: { availability: "ok", risk_mmod: 0.2 },
      forecast: { available: true, covers_horizon: true, windows: [{ start: "a", end: "b", eva_allowed: true }] },
      metadata: { completeness: 1, sources: [{ status: "ok" }] },
    });
    expect(brief.status).toBe("ok");
    expect(brief.title).toBe("Безопасный выход");
    expect(brief.reason).toMatch(/выход разрешён/);
  });
});

describe("plan change", () => {
  it("labels duration change as a different plan", () => {
    const note = describePlanChange(
      { mode: "historical", start: "2024-06-15T08:00:00Z", duration: 8 },
      { mode: "historical", start: "2024-06-15T08:00:00Z", duration: 4 },
      { recommendation: { score: 0.4 } },
      { recommendation: { score: 0.8 } },
    );
    expect(note).toMatch(/смена плана/);
    expect(note).toMatch(/разные планы/);
  });
});

describe("sources", () => {
  it("resolves warning URL from source_url then name", () => {
    expect(resolveWarningUrl({ source_url: "https://example.test", source: "x" })).toBe("https://example.test");
    expect(resolveWarningUrl({ source: "NASA DONKI / WSA" })).toMatch(/DONKI/);
  });

  it("maps disabled/frozen controls into the request", () => {
    const q = applyControlsToRequest(defaultQuery("current"), {
      "NOAA SWPC": { disabled: true, frozen: true },
    });
    expect(q.disabled_sources).toEqual(["NOAA SWPC"]);
    expect(q.frozen_sources).toEqual(["NOAA SWPC"]);
  });
});

describe("UTC formatting", () => {
  it("builds ISO from date+time and labels UTC", () => {
    expect(dateTimeToIso("2024-06-15", "08:00")).toBe("2024-06-15T08:00:00Z");
    expect(dateTimeToIso("15.06.2024", "08:00")).toBe("2024-06-15T08:00:00Z");
    expect(dateTimeToIso("15-06-2024", "08:00")).toBe("2024-06-15T08:00:00Z");
    expect(dateTimeToIso("15/06/2024", "08:00")).toBe("2024-06-15T08:00:00Z");
    expect(formatUtc("2024-06-15T08:00:00Z")).toBe("15.06.2024 08:00:00 UTC");
  });
});
