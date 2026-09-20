"""Shared constants for the CosmoHACK Orbit API."""

from __future__ import annotations

ISS_NORAD = "25544"
CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
CELESTRAK_SATCAT_URL = "https://celestrak.org/satcat/records.php"
USER_AGENT = "Mozilla/5.0 (compatible; CosmoHACK-OrbitAPI/1.0)"
SOCRATES_CACHE_TTL_SECONDS = 2 * 60 * 60
ORBITAL_ELEMENTS_CACHE_TTL_SECONDS = 2 * 60 * 60
SATCAT_CACHE_TTL_SECONDS = 2 * 60 * 60
OMM_TIMEOUT = 30.0
MAX_CONCURRENT_OMM = 5
TCA_MARGIN_SECONDS = 120
SOCRATES_SCREENING_KM = 5.0
STEP_SECONDS = 60
MAX_WINDOW_SECONDS = 86400
# Verified catalog IDs of ISS-complex vehicles (not name-based heuristics).
ISS_ASSOCIATED_NORAD_IDS: set[str] = {"100057"}
ISS_CENTER_MARKERS = {
    "25544", "ISS", "ZARYA", "ISS (ZARYA)", "ISS-ZARYA",
    "1998-067A", "INTERNATIONAL SPACE STATION",
}
OMM_REQUIRED = {"NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION"}
LOGGER_NAME = "orbit_api"
