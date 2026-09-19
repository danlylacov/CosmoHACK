import JSZip from "jszip";
import { ALGORITHM_VERSION } from "../config.js";

function csvCell(value) {
  if (value == null || value === "") return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  const s = String(value);
  if (/[",\n\r]/.test(s)) return `"${s.replaceAll('"', '""')}"`;
  return s;
}

function toCsv(rows) {
  return `\uFEFF${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
}

export function snapshotData(data) {
  if (!data) return null;
  return JSON.parse(JSON.stringify(data));
}

export function exportStamp(data) {
  const start = String(data?.request?.start || data?.query_id || "export")
    .replaceAll(":", "")
    .replace(/\.\d+Z$/, "Z");
  return `vkd-${start}`;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function windowsCsv(data) {
  const header = [
    "start_utc",
    "end_utc",
    "score",
    "risk_sw",
    "risk_mmod",
    "eva_allowed",
    "blocked",
    "distance_min_km",
    "overlap_sw_h",
    "overlap_mmod_h",
    "completeness",
    "contains_conjunction",
    "requires_check",
    "is_recommended",
  ];
  const rec = data.recommendation?.window;
  const recKey = rec ? `${rec.start}|${rec.end}` : "";
  const rows = (data.windows || []).map((w) => [
    w.start,
    w.end,
    w.score,
    w.risk_sw,
    w.risk_mmod,
    w.eva_allowed,
    w.blocked,
    w.distance_min_km,
    w.overlap_sw,
    w.overlap_mmod,
    w.completeness,
    w.contains_conjunction,
    w.requires_check,
    `${w.start}|${w.end}` === recKey,
  ]);
  return toCsv([header, ...rows]);
}

export function warningsCsv(data) {
  const header = [
    "id",
    "type",
    "mechanism",
    "title",
    "source",
    "source_url",
    "published_at",
    "event_time",
    "period_start",
    "period_end",
    "value",
    "unit",
    "impact",
    "rule",
    "limitations",
    "confidence",
    "intersects_window",
  ];
  const all = [...(data.risk_sw?.warnings ?? []), ...(data.risk_mmod?.warnings ?? [])];
  const rows = all.map((w) => [
    w.id,
    w.type,
    w.mechanism,
    w.title,
    w.source,
    w.source_url,
    w.published_at,
    w.event_time,
    w.period?.start ?? "",
    w.period?.end ?? "",
    w.value,
    w.unit,
    w.impact,
    w.rule,
    w.limitations,
    w.confidence,
    w.intersects_window,
  ]);
  return toCsv([header, ...rows]);
}

export function sourcesCsv(data) {
  const header = [
    "name",
    "url",
    "fetched_at",
    "age",
    "status",
    "enabled",
    "frozen",
    "unsuitable_for_replay",
  ];
  const rows = (data.metadata?.sources ?? []).map((s) => [
    s.name,
    s.url,
    s.fetched_at,
    s.age,
    s.status,
    s.enabled !== false,
    Boolean(s.frozen),
    Boolean(s.unsuitable_for_replay),
  ]);
  return toCsv([header, ...rows]);
}

export function metricsCsv(data) {
  const sw = data.risk_sw?.metrics || {};
  const mm = data.risk_mmod?.metrics || {};
  const peak = data.forecast?.peak || {};
  const rows = [
    ["key", "value"],
    ["exported_at", new Date().toISOString().replace(/\.\d{3}Z$/, "Z")],
    ["query_start", data.request?.start ?? ""],
    ["duration_h", data.request?.duration ?? ""],
    ["period_h", data.request?.period ?? ""],
    ["risk_sw", data.risk_sw?.risk_sw ?? ""],
    ["availability_sw", data.risk_sw?.availability ?? ""],
    ["kp", sw.kp ?? peak.kp ?? ""],
    ["sep_10mev", sw.sep_10mev ?? ""],
    ["sep_50mev", sw.sep_50mev ?? ""],
    ["sep_100mev", sw.sep_100mev ?? ""],
    ["xray_wm2", sw.xray_wm2 ?? ""],
    ["p_adverse", sw.p_adverse ?? ""],
    ["s_level", peak.s ?? ""],
    ["g_level", peak.g ?? ""],
    ["r_level", peak.r ?? ""],
    ["eva_allowed_all", data.forecast?.eva_allowed_all ?? ""],
    ["forecast_covers_horizon", data.forecast?.covers_horizon ?? ""],
    ["forecast_origin_utc", data.forecast?.forecast_origin_utc ?? ""],
    ["risk_mmod", data.risk_mmod?.risk_mmod ?? ""],
    ["availability_mmod", data.risk_mmod?.availability ?? ""],
    ["miss_distance_km", mm.miss_distance ?? ""],
    ["relative_speed_km_s", mm.relative_speed_km_s ?? ""],
    ["tca", mm.tca ?? ""],
    ["pc", mm.pc ?? ""],
    ["recommended_start", data.recommendation?.window?.start ?? ""],
    ["recommended_end", data.recommendation?.window?.end ?? ""],
    ["recommended_score", data.recommendation?.score ?? ""],
  ];
  return toCsv(rows);
}

export function manifest(data) {
  return {
    exported_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    timezone: "UTC",
    query_id: data.query_id ?? null,
    algorithm_version: data.algorithm_version || ALGORITHM_VERSION,
    request: data.request ?? null,
    cutoff_time: data.request?.cutoff_time ?? null,
    historical_intent: data.request?.historical_intent ?? null,
    recommendation: data.recommendation ?? null,
    factors: {
      risk_sw: data.risk_sw?.risk_sw ?? null,
      availability_sw: data.risk_sw?.availability ?? null,
      risk_mmod: data.risk_mmod?.risk_mmod ?? null,
      availability_mmod: data.risk_mmod?.availability ?? null,
    },
    forecast: {
      available: data.forecast?.available ?? false,
      covers_horizon: data.forecast?.covers_horizon ?? null,
      origin: data.forecast?.forecast_origin_utc ?? null,
      eva_allowed_all: data.forecast?.eva_allowed_all ?? null,
    },
    eva: {
      available: data.eva?.available ?? false,
      windows: data.eva?.windows?.length ?? 0,
    },
    source_versions: (data.metadata?.sources ?? []).map((s) => ({
      name: s.name,
      url: s.url,
      fetched_at: s.fetched_at,
      status: s.status,
      enabled: s.enabled !== false,
    })),
    guarantee: "Значения совпадают с отображаемыми в интерфейсе на момент выгрузки.",
  };
}

export function buildExportBundle(data) {
  const snapshot = snapshotData(data);
  const json = `${JSON.stringify(snapshot, null, 2)}\n`;
  return {
    snapshot,
    json,
    csv: windowsCsv(snapshot),
    files: {
      "analyze.json": json,
      "windows.csv": windowsCsv(snapshot),
      "metrics.csv": metricsCsv(snapshot),
      "warnings.csv": warningsCsv(snapshot),
      "sources.csv": sourcesCsv(snapshot),
      "manifest.json": `${JSON.stringify(manifest(snapshot), null, 2)}\n`,
    },
  };
}

export function exportPanelHTML() {
  return `
    <div class="stack">
      <p class="export-note">
        Выгрузка совпадает с тем, что на экране: JSON — полный расчёт, CSV — таблица окон, ZIP — JSON, окна, метрики, предупреждения и источники.
      </p>
      <div class="btn-row">
        <button class="btn" type="button" data-export="json">Скачать JSON</button>
        <button class="btn" type="button" data-export="csv">Скачать CSV</button>
        <button class="btn btn--primary" type="button" data-export="zip">Скачать ZIP</button>
      </div>
    </div>
  `;
}

export async function runExport(kind, data) {
  if (!data) throw new Error("Нет расчёта для выгрузки.");
  const stamp = exportStamp(data);
  const bundle = buildExportBundle(data);

  if (kind === "json") {
    downloadBlob(new Blob([bundle.json], { type: "application/json;charset=utf-8" }), `${stamp}.json`);
    return;
  }
  if (kind === "csv") {
    downloadBlob(new Blob([bundle.csv], { type: "text/csv;charset=utf-8" }), `${stamp}-windows.csv`);
    return;
  }
  if (kind === "zip") {
    const zip = new JSZip();
    for (const [name, contents] of Object.entries(bundle.files)) {
      zip.file(name, contents);
    }
    const blob = await zip.generateAsync({ type: "blob", compression: "DEFLATE" });
    downloadBlob(blob, `${stamp}.zip`);
    return;
  }
  throw new Error("Неизвестный формат выгрузки.");
}
