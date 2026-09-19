import { describe, expect, it } from "vitest";
import JSZip from "jszip";
import {
  buildExportBundle,
  exportStamp,
  snapshotData,
  windowsCsv,
} from "../../components/ExportPanel.js";

const data = {
  query_id: "q-test",
  request: { start: "2026-09-19T19:00:00Z", duration: 6, period: 12 },
  recommendation: { window: { start: "2026-09-19T22:00:00Z", end: "2026-09-20T04:00:00Z" }, score: 1 },
  windows: [
    {
      start: "2026-09-19T22:00:00Z",
      end: "2026-09-20T04:00:00Z",
      score: 1,
      risk_sw: 0.12,
      risk_mmod: 0.09,
      eva_allowed: true,
      blocked: false,
      distance_min_km: 78.8,
      overlap_sw: 0,
      overlap_mmod: 0,
      completeness: 1,
      contains_conjunction: false,
      requires_check: false,
    },
  ],
  risk_sw: {
    risk_sw: 0.12,
    availability: "ok",
    metrics: { kp: 1.8, sep_10mev: 0.3, cme_speed: null },
    warnings: [{ id: "w-sw-forecast-policy", title: "Погода: выход разрешён", mechanism: "radiation" }],
  },
  risk_mmod: {
    risk_mmod: 0.46,
    availability: "ok",
    metrics: { miss_distance: 5.4, tca: "2026-09-20T04:36:53Z", pc: null, relative_speed_km_s: 11.2 },
    warnings: [],
  },
  forecast: { available: true, covers_horizon: true, eva_allowed_all: true, peak: { s: 0, g: 0, r: 0, kp: 1.8 } },
  eva: { available: true, windows: [{ start: "2026-09-19T22:00:00Z" }] },
  metadata: { sources: [{ name: "CosmoHACK EVA API", url: "http://example.test", status: "ok", fetched_at: "2026-09-19T19:00:00Z" }] },
};

describe("export bundle", () => {
  it("stamps the filename from the query start", () => {
    expect(exportStamp(data)).toBe("vkd-2026-09-19T190000Z");
  });

  it("JSON snapshot keeps live SW/MMOD/EVA fields and drops nothing serializable", () => {
    const snap = snapshotData(data);
    expect(snap.windows[0].distance_min_km).toBe(78.8);
    expect(snap.forecast.eva_allowed_all).toBe(true);
    expect(snap.risk_mmod.metrics.pc).toBeNull();
    expect(JSON.parse(buildExportBundle(data).json).recommendation.score).toBe(1);
  });

  it("CSV is a UTF-8 table of the window comparison including eva_allowed", () => {
    const csv = windowsCsv(data);
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toMatch(/eva_allowed/);
    expect(csv).toMatch(/2026-09-19T22:00:00Z/);
    expect(csv).toMatch(/true/);
    expect(csv).toMatch(/78.8/);
  });

  it("ZIP files include JSON, windows, metrics, warnings and sources", async () => {
    const bundle = buildExportBundle(data);
    expect(Object.keys(bundle.files).sort()).toEqual([
      "analyze.json",
      "manifest.json",
      "metrics.csv",
      "sources.csv",
      "warnings.csv",
      "windows.csv",
    ]);
    const zip = new JSZip();
    for (const [name, contents] of Object.entries(bundle.files)) zip.file(name, contents);
    const blob = await zip.generateAsync({ type: "uint8array" });
    const opened = await JSZip.loadAsync(blob);
    const json = JSON.parse(await opened.file("analyze.json").async("string"));
    const metrics = await opened.file("metrics.csv").async("string");
    expect(json.risk_mmod.metrics.miss_distance).toBe(5.4);
    expect(metrics).toMatch(/miss_distance_km,5.4/);
    expect(await opened.file("sources.csv").async("string")).toMatch(/CosmoHACK EVA API/);
  });
});
