"""Pure SATCAT field lookup and ISS-association classification."""

from __future__ import annotations

from constants import ISS_ASSOCIATED_NORAD_IDS, ISS_CENTER_MARKERS, ISS_NORAD


def satcat_field(record: dict, *names: str) -> str:
    lower = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        value = record.get(name)
        if value is None:
            value = lower.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def is_iss_associated_record(record: dict) -> bool:
    orbit_type = satcat_field(record, "ORBIT_TYPE", "Orbit Type").upper()
    orbit_center = satcat_field(record, "ORBIT_CENTER", "Orbit Center").upper()
    ops_status = satcat_field(
        record, "OPS_STATUS_CODE", "OPS_STATUS", "Operational Status"
    ).upper()
    data_status = satcat_field(record, "DATA_STATUS_CODE", "Data Status Code").upper()

    if orbit_type in {"DOC", "DOCKED", "D"} or "DOCK" in orbit_type:
        return True
    if "DOCK" in ops_status or "DOCK" in data_status:
        return True
    center_compact = orbit_center.replace("_", " ")
    for marker in ISS_CENTER_MARKERS:
        if marker == orbit_center or marker in center_compact:
            return True
    if orbit_center in ISS_ASSOCIATED_NORAD_IDS or orbit_center == ISS_NORAD:
        return True
    return False


def classify_norad(norad_id: str, satcat_record: dict | None, _satcat_failed: bool) -> str:
    if satcat_record:
        if is_iss_associated_record(satcat_record):
            return "ISS_ASSOCIATED"
        return "EXTERNAL_CONJUNCTION"
    if norad_id in ISS_ASSOCIATED_NORAD_IDS:
        return "ISS_ASSOCIATED"
    return "UNKNOWN"
