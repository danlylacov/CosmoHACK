import { describe, expect, it } from "vitest";
import { applyEvaWindows, evaBestWindow, evaStatusText, evaWindowTone, overlayWindows, toEvaWindowsRequest } from "../eva.js";

const payload = {
  request: {
    start_time: "2026-09-20T03:30:00Z",
    end_time: "2026-09-20T05:30:00Z",
    duration_min: 30,
    step_min: 1,
    top_k: 5,
    critical_distance_km: 5,
  },
  horizon_minutes: 120,
  windows: [
    {
      start: "2026-09-20T03:48:00Z",
      end: "2026-09-20T04:18:00Z",
      status: "SAFE",
      safety: 1,
      danger: 0,
      peak_danger: 0,
      average_danger: 0,
      critical_overlap_seconds: 0,
      data_coverage: 1,
      distance_min_km: 264.194,
      blocked: false,
      reasons: ["Минимальная дистанция до отслеживаемого объекта — 264.194 км"],
    },
    {
      start: "2026-09-20T04:48:00Z",
      end: "2026-09-20T05:18:00Z",
      status: "SAFE",
      safety: 0.9,
      danger: 0.1,
      peak_danger: 0.1,
      average_danger: 0.05,
      data_coverage: 1,
      distance_min_km: 4113.478,
      blocked: false,
      reasons: [],
    },
  ],
  data_quality: {
    status: "COMPLETE",
    warnings: ["Погодные факторы пока не участвовали в расчёте."],
    elements_epoch: "2026-09-19T07:08:00Z",
    calculated_at: "2026-09-19T16:56:48Z",
  },
};

describe("toEvaWindowsRequest", () => {
  it("maps hours to minutes and clamps critical distance to 5 km", () => {
    const req = toEvaWindowsRequest({
      start: "2026-09-20T03:30:00Z",
      duration: 6,
      period: 12,
      critical_distance_km: 80,
    });
    expect(req.start_time).toBe("2026-09-20T03:30:00Z");
    expect(req.end_time).toBe("2026-09-20T15:30:00Z");
    expect(req.duration_min).toBe(360);
    expect(req.step_min).toBe(1);
    expect(req.top_k).toBe(5);
    expect(req.critical_distance_km).toBe(5);
  });
});

describe("applyEvaWindows", () => {
  const base = {
    request: { start: "2026-09-20T03:30:00Z", duration: 0.5, period: 2, critical_distance_km: 80 },
    windows: [{ start: "mock-a", end: "mock-b", score: 0.4 }],
    recommendation: { window: { start: "mock-a", end: "mock-b" }, reason: "мок", limitations: "SW mock" },
    metadata: { sources: [] },
  };

  it("replaces mock windows with EVA ranking", () => {
    const data = applyEvaWindows(base, payload);
    expect(data.windows[0].start).toBe("2026-09-20T03:48:00Z");
    expect(data.windows[0].score).toBe(1);
    expect(data.windows[0].risk_mmod).toBe(0);
    expect(data.eva.available).toBe(true);
    expect(data.eva.windows).toHaveLength(2);
    expect(data.recommendation.window.start).toBe("2026-09-20T03:48:00Z");
    expect(data.recommendation.reason).toBe("мок");
    expect(data.eva.windows[0].distance_min_km).toBe(264.194);
    expect(data.eva.data_quality.warnings.some((w) => /ограничен 5 км/.test(w))).toBe(true);
    expect(data.metadata.sources.some((s) => s.name === "CosmoHACK EVA API")).toBe(true);
  });

  it("does not fail the analyze payload when EVA errors", () => {
    const data = applyEvaWindows(base, null, { message: "EVA down" });
    expect(data.windows[0].start).toBe("mock-a");
    expect(data.eva.available).toBe(false);
    expect(data.eva.error).toBe("EVA down");
    expect(data.recommendation.window.start).toBe("mock-a");
  });
});

describe("evaWindowTone", () => {
  it("treats blocked or unsafe as danger", () => {
    expect(evaWindowTone({ status: "SAFE", blocked: true })).toBe("danger");
    expect(evaWindowTone({ status: "UNSAFE" })).toBe("danger");
    expect(evaWindowTone({ status: "SAFE" })).toBe("ok");
  });
});

describe("eva brief helpers", () => {
  it("picks the ranked slot for the short summary", () => {
    const data = applyEvaWindows(
      {
        request: { start: "2026-09-20T03:30:00Z" },
        recommendation: { window: { start: "mock-a", end: "mock-b" } },
        metadata: { sources: [] },
      },
      payload,
    );
    const best = evaBestWindow(data);
    expect(best.start).toBe("2026-09-20T03:48:00Z");
    expect(evaStatusText(best)).toBe("Безопасно");
    expect(overlayWindows(data)[0].start).toBe(best.start);
  });
});
