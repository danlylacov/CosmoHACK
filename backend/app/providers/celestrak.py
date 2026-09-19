from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup
from sgp4.api import Satrec
from sgp4.conveniences import sat_epoch_datetime

from app.core.errors import OrbitalElementsError, SourceUnavailableError
from app.providers.base import OrbitalElements, OrbitalElementsProvider, OrbitalElementsSnapshot

ISS_NORAD_ID = 25544
SOCRATES_URL = "https://celestrak.org/SOCRATES/table-socrates.php"
GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
USER_AGENT = "CosmoHACK-Orbit-Service/1.0"
GP_CONNECTIONS = 8


class CelesTrakOrbitalElementsProvider(OrbitalElementsProvider):
    def __init__(self, *, timeout_seconds: float = 30.0, max_records: int = 100) -> None:
        self._timeout = timeout_seconds
        self._max_records = max_records

    async def get_snapshot(self) -> OrbitalElementsSnapshot:
        retrieved_at = datetime.now(UTC)
        # #region agent log
        _t0 = __import__("time").monotonic()
        try:
            import json as _json, time as _time
            with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"A","location":"celestrak.py:get_snapshot:start","message":"CelesTrak snapshot fetch started","data":{"max_records":self._max_records,"timeout":self._timeout},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                limits=httpx.Limits(
                    max_connections=GP_CONNECTIONS,
                    max_keepalive_connections=GP_CONNECTIONS,
                ),
            ) as client:
                candidates = await self._fetch_candidate_ids(client)
                ids = [ISS_NORAD_ID, *sorted(candidates - {ISS_NORAD_ID})]
                # #region agent log
                try:
                    import json as _json, time as _time
                    with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                        _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"A","location":"celestrak.py:get_snapshot:candidates","message":"SOCRATES candidates parsed","data":{"n_candidates":len(candidates),"n_ids":len(ids),"elapsed_s":round(__import__("time").monotonic()-_t0,3)},"timestamp":int(_time.time()*1000)})+"\n")
                except Exception:
                    pass
                # #endregion
                results = await asyncio.gather(
                    *(self._fetch_elements(client, norad_id, retrieved_at) for norad_id in ids),
                    return_exceptions=True,
                )
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"CelesTrak request failed: {exc}") from exc
        # #region agent log
        try:
            import json as _json, time as _time
            _n_err = sum(1 for r in results if isinstance(r, Exception))
            with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"A","location":"celestrak.py:get_snapshot:end","message":"CelesTrak snapshot fetch finished","data":{"n_ids":len(ids),"n_errors":_n_err,"elapsed_s":round(__import__("time").monotonic()-_t0,3)},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion

        elements: list[OrbitalElements] = []
        warnings: list[str] = []
        for norad_id, result in zip(ids, results, strict=True):
            if isinstance(result, Exception):
                if norad_id == ISS_NORAD_ID:
                    if isinstance(result, SourceUnavailableError | OrbitalElementsError):
                        raise result
                    raise OrbitalElementsError(f"Could not load ISS elements: {result}") from result
                warnings.append(f"NORAD {norad_id} skipped: {result}")
                continue
            elements.append(result)

        return OrbitalElementsSnapshot(
            elements=tuple(elements),
            retrieved_at=retrieved_at,
            source="CelesTrak",
            warnings=tuple(warnings),
        )

    async def _fetch_candidate_ids(self, client: httpx.AsyncClient) -> set[int]:
        try:
            response = await client.get(
                SOCRATES_URL,
                params={
                    "CATNR": f"{ISS_NORAD_ID},",
                    "ORDER": "MINRANGE",
                    "MAX": self._max_records,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"SOCRATES is unavailable: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        ids: set[int] = set()
        row_ids: list[int] = []
        for row in soup.select("tr"):
            values = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            if len(values) > 1 and values[1].isdigit():
                row_ids.append(int(values[1]))
        for first, second in zip(row_ids[::2], row_ids[1::2], strict=False):
            if first == ISS_NORAD_ID and second != ISS_NORAD_ID:
                ids.add(second)
            elif second == ISS_NORAD_ID and first != ISS_NORAD_ID:
                ids.add(first)
        return ids

    async def _fetch_elements(
        self,
        client: httpx.AsyncClient,
        norad_id: int,
        retrieved_at: datetime,
    ) -> OrbitalElements:
        tle_response = await client.get(GP_URL, params={"CATNR": norad_id, "FORMAT": "TLE"})
        try:
            tle_response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"NORAD {norad_id} could not be fetched: {exc}") from exc

        name, line1, line2 = self._parse_tle(tle_response.text, norad_id)
        try:
            epoch = sat_epoch_datetime(Satrec.twoline2rv(line1, line2)).astimezone(UTC)
        except (TypeError, ValueError) as exc:
            raise OrbitalElementsError(f"Invalid TLE for NORAD {norad_id}: {exc}") from exc

        return OrbitalElements(
            norad_id=norad_id,
            name=name.strip(),
            object_type=self._object_type_from_name(name),
            tle_line1=line1,
            tle_line2=line2,
            epoch=epoch,
            retrieved_at=retrieved_at,
            source="CelesTrak",
        )

    @staticmethod
    def _parse_tle(text: str, norad_id: int) -> tuple[str, str, str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 3 or not lines[-2].startswith("1 ") or not lines[-1].startswith("2 "):
            raise OrbitalElementsError(f"TLE is missing for NORAD {norad_id}")
        return lines[-3], lines[-2], lines[-1]

    @staticmethod
    def _object_type_from_name(name: str) -> str | None:
        upper = name.upper()
        if "DEB" in upper:
            return "DEBRIS"
        if "R/B" in upper:
            return "ROCKET_BODY"
        return None
