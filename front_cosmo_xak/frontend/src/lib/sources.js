export function resolveWarningUrl(warning, sources = []) {
  if (warning?.source_url) return warning.source_url;
  const name = String(warning?.source || "").toLowerCase();
  const hit = sources.find((s) => {
    const src = String(s.name || "").toLowerCase();
    return name.includes(src.slice(0, 12).toLowerCase()) || src.includes(name.slice(0, 8));
  });
  if (hit?.url) return hit.url;

  if (name.includes("donki")) return "https://kauai.ccmc.gsfc.nasa.gov/DONKI/";
  if (name.includes("swpc") || name.includes("goes") || name.includes("noaa")) {
    return "https://services.swpc.noaa.gov/";
  }
  if (name.includes("space-track") || name.includes("cdm")) return "https://www.space-track.org/";
  if (name.includes("iau")) return "https://www.ta3.sk/IAUC22DB/MDC2007/";
  if (name.includes("celestrak")) return "https://celestrak.org/NORAD/elements/";
  return null;
}

export function sourceKey(name) {
  return String(name || "").trim();
}

export function applyControlsToRequest(query, sourceControls = {}) {
  const disabled_sources = [];
  const frozen_sources = [];
  for (const [name, flags] of Object.entries(sourceControls)) {
    if (flags?.disabled) disabled_sources.push(name);
    if (flags?.frozen) frozen_sources.push(name);
  }
  return { ...query, disabled_sources, frozen_sources };
}
