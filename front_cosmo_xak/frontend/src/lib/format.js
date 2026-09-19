export function pad(n, w = 2) {
  return String(n).padStart(w, "0");
}

export function parseIso(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

export function parseFlexibleDate(raw) {
  const s = String(raw || "").trim();
  const ymd = s.match(/^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$/);
  if (ymd) return { y: Number(ymd[1]), mo: Number(ymd[2]), d: Number(ymd[3]) };
  const dmy = s.match(/^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$/);
  if (dmy) return { d: Number(dmy[1]), mo: Number(dmy[2]), y: Number(dmy[3]) };
  return null;
}

export function formatUtc(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}

export function formatUtcShort(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

export function isoToDateInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
}

export function isoToTimeInput(iso) {
  if (!iso) return "";
  return iso.slice(11, 16);
}

export function dateTimeToIso(date, time) {
  const parsed = parseFlexibleDate(date);
  if (!parsed || !time) return "";
  const { y, mo, d } = parsed;
  const hhmm = time.length === 5 ? `${time}:00` : time;
  const iso = `${y}-${pad(mo)}-${pad(d)}T${hhmm}Z`;
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() + 1 !== mo || dt.getUTCDate() !== d) return "";
  return iso;
}

export function addHoursIso(startIso, hours) {
  const t = Date.parse(startIso);
  if (Number.isNaN(t)) return "";
  return new Date(t + Number(hours) * 3600_000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function scrubberLabel(startIso, hours) {
  const clock = formatUtc(addHoursIso(startIso, hours));
  return `${clock} · +${Number(hours).toFixed(2)} ч`;
}

export function hoursFromStart(iso, startIso) {
  return (Date.parse(iso) - Date.parse(startIso)) / 3600_000;
}

export function formatHours(h) {
  if (h == null || Number.isNaN(h)) return "—";
  const sign = h < 0 ? "−" : "";
  const abs = Math.abs(h);
  const hh = Math.floor(abs);
  const mm = Math.round((abs - hh) * 60);
  return `${sign}${hh}ч ${pad(mm)}м`;
}

export function formatNumber(n, digits = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatSci(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toExponential(1);
}

export function pct(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

export function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
