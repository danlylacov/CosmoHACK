/** TEME km → geodetic for the textured Earth (GMST rotation, spherical Earth). */

const EARTH_KM = 6371;

function julianDate(date) {
  return date.getTime() / 86400000 + 2440587.5;
}

/** Greenwich mean sidereal time, radians. */
export function gmstRadians(date) {
  const jd = julianDate(date);
  const t = jd - 2451545.0;
  let deg = 280.46061837 + 360.98564736629 * t;
  deg = ((deg % 360) + 360) % 360;
  return (deg * Math.PI) / 180;
}

export function temeToEcef(x, y, z, date) {
  const theta = gmstRadians(date);
  const c = Math.cos(theta);
  const s = Math.sin(theta);
  return {
    x: x * c + y * s,
    y: -x * s + y * c,
    z,
  };
}

export function temeToGeodetic(x, y, z, iso) {
  const date = new Date(iso);
  const ecef = temeToEcef(x, y, z, date);
  const r = Math.hypot(ecef.x, ecef.y, ecef.z) || 1;
  return {
    lat: (Math.asin(ecef.z / r) * 180) / Math.PI,
    lon: (Math.atan2(ecef.y, ecef.x) * 180) / Math.PI,
    alt_km: r - EARTH_KM,
  };
}

export function temeDistanceKm(a, b) {
  if (!a || !b) return Infinity;
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}
