import { slotEvaAllowed } from "./forecast.js";

/** Risk SW: < 0.35 safe exit, > 0.6 forbidden, in between — extra check. */
export const SW_SAFE_MAX = 0.35;
export const SW_BLOCK_MIN = 0.6;

/**
 * Never display "all safe" when data are missing, stale, partial, or a mechanism is disabled.
 */
export function riskTone(value, availability = "ok") {
  if (availability && availability !== "ok") return "unknown";
  if (value == null || Number.isNaN(Number(value))) return "unknown";
  const v = Number(value);
  if (v > SW_BLOCK_MIN) return "danger";
  if (v >= SW_SAFE_MAX) return "warn";
  return "ok";
}

function worseTone(a, b) {
  const rank = { empty: -1, ok: 0, warn: 1, unknown: 1, danger: 2 };
  return (rank[a] ?? 0) >= (rank[b] ?? 0) ? a : b;
}

export function recommendedSlot(data) {
  const rec = data?.recommendation?.window;
  if (rec?.start && rec?.end) {
    const row = (data.windows || []).find((w) => w.start === rec.start && w.end === rec.end);
    return row ? { ...row, start: rec.start, end: rec.end } : rec;
  }
  if (data?.eva?.available && data.eva.windows?.[0]) return data.eva.windows[0];
  return data?.windows?.[0] || null;
}

function swToneForSlot(data, slot) {
  if (data?.forecast?.available === false) return "unknown";
  if (data?.forecast?.covers_horizon === false) return "unknown";
  const av = data?.risk_sw?.availability;
  if (av && av !== "ok") return "unknown";
  const value = slot?.risk_sw ?? data?.risk_sw?.risk_sw;
  return riskTone(value, "ok");
}

function mmodToneForSlot(data, slot) {
  const av = data?.risk_mmod?.availability || "ok";
  const value = slot?.risk_mmod ?? data?.risk_mmod?.risk_mmod;
  return riskTone(value, av);
}

export function briefRecommendation(data) {
  if (!data) {
    return { status: "empty", title: "Нет данных", window: null, reason: "", caveat: null };
  }
  const slot = recommendedSlot(data);
  const allowed = slot?.eva_allowed === false ? false : slotEvaAllowed(data, slot);
  const evaBlocked = Boolean(slot?.blocked);
  const sw = swToneForSlot(data, slot);
  const mmod = mmodToneForSlot(data, slot);
  let status = "ok";
  if (evaBlocked || allowed === false || sw === "danger" || mmod === "danger") status = "danger";
  else status = worseTone(sw, mmod);
  if (status !== "danger" && (sw === "unknown" || mmod === "unknown" || !slot)) status = "warn";
  if (data.status === "error") status = "danger";
  else if (status === "ok" && !dataReliable(data)) status = "warn";

  const title =
    status === "danger"
      ? "Выход запрещён"
      : status === "ok"
        ? "Безопасный выход"
        : status === "empty"
          ? "Нет данных"
          : "Внимание, требуется дополнительная проверка";

  let caveat = null;
  if (sw === "unknown" || data.forecast?.covers_horizon === false) {
    caveat = "По погоде в этом окне нет оценки: нельзя читать результат как «всё безопасно».";
  } else if (data.status === "partial" || (data.metadata?.completeness ?? 1) < 0.8) {
    caveat = "Данных не хватает: нельзя читать результат как «всё безопасно».";
  }

  return {
    status: status === "unknown" ? "warn" : status,
    title,
    window: slot ? { start: slot.start, end: slot.end } : null,
    reason: data.recommendation?.reason || "",
    distance_min_km: slot?.distance_min_km ?? null,
    caveat,
  };
}

export function dataReliable(data) {
  if (!data) return false;
  if (data.status === "error" || data.status === "partial") return false;
  if ((data.metadata?.completeness ?? 0) < 0.8) return false;
  const sources = data.metadata?.sources ?? [];
  if (sources.some((s) => s.status === "error" || s.enabled === false)) return false;
  if (data.risk_sw?.availability && data.risk_sw.availability !== "ok") return false;
  if (data.risk_mmod?.availability && data.risk_mmod.availability !== "ok") return false;
  return true;
}

export function overallStatus(data) {
  if (!data) return "empty";
  if (data.status === "error") return "danger";

  const brief = briefRecommendation(data);
  if (brief.status === "empty") return "warn";
  return brief.status;
}

export function statusLabel(status) {
  if (status === "ok") return { emoji: "🟢", text: "OK" };
  if (status === "warn") return { emoji: "🟡", text: "Внимание" };
  if (status === "danger") return { emoji: "🔴", text: "Опасно" };
  return { emoji: "⚪", text: "Нет данных" };
}

export function collectWarnings(data) {
  if (!data) return [];
  return [...(data.risk_sw?.warnings ?? []), ...(data.risk_mmod?.warnings ?? [])];
}

export function availabilityNote(availability) {
  if (availability === "disabled") {
    return "Источник механизма отключён. Это не нулевой риск, а отказ от оценки.";
  }
  if (availability === "insufficient_data") {
    return "Недостаточно данных для оценки. Пропуск наблюдений ≠ отсутствие воздействия.";
  }
  return null;
}
