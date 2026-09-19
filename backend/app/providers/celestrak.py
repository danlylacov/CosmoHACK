from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sgp4.api import Satrec
from sgp4.conveniences import sat_epoch_datetime

from app.core.errors import OrbitalElementsError, SourceUnavailableError
from app.providers.base import OrbitalElements, OrbitalElementsProvider, OrbitalElementsSnapshot

ISS_NORAD_ID = 25544
SOCRATES_URL = "https://celestrak.org/SOCRATES/table-socrates.php"
GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
SATCAT_URL = "https://celestrak.org/satcat/records.php"
USER_AGENT = "CosmoHACK-Orbit-Service/1.0"


class CelesTrakOrbitalElementsProvider(OrbitalElementsProvider):
    def __init__(self, *, timeout_seconds: float = 30.0, max_records: int = 100) -> None:
        self._timeout = timeout_seconds
        self._max_records = max_records

    async def get_snapshot(self) -> OrbitalElementsSnapshot:
        retrieved_at = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                candidates = await self._fetch_candidate_ids(client)
                ids = [ISS_NORAD_ID, *sorted(candidates - {ISS_NORAD_ID})]
                results = await asyncio.gather(
                    *(self._fetch_elements(client, norad_id, retrieved_at) for norad_id in ids),
                    return_exceptions=True,
                )
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"CelesTrak request failed: {exc}") from exc

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
        tle_response, satcat_response = await asyncio.gather(
            client.get(GP_URL, params={"CATNR": norad_id, "FORMAT": "TLE"}),
            client.get(SATCAT_URL, params={"CATNR": norad_id, "FORMAT": "JSON"}),
        )
        try:
            tle_response.raise_for_status()
            satcat_response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"NORAD {norad_id} could not be fetched: {exc}") from exc

        name, line1, line2 = self._parse_tle(tle_response.text, norad_id)
        metadata = self._parse_satcat(satcat_response, norad_id)
        try:
            epoch = sat_epoch_datetime(Satrec.twoline2rv(line1, line2)).astimezone(UTC)
        except (TypeError, ValueError) as exc:
            raise OrbitalElementsError(f"Invalid TLE for NORAD {norad_id}: {exc}") from exc

        return OrbitalElements(
            norad_id=norad_id,
            name=str(metadata.get("OBJECT_NAME") or name).strip(),
            object_type=self._normalize_object_type(metadata.get("OBJECT_TYPE")),
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
    def _parse_satcat(response: httpx.Response, norad_id: int) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise OrbitalElementsError(f"Invalid SATCAT metadata for NORAD {norad_id}") from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise OrbitalElementsError(f"SATCAT metadata is missing for NORAD {norad_id}")
        return payload[0]

    @staticmethod
    def _normalize_object_type(value: Any) -> str | None:
        mapping = {"PAY": "PAYLOAD", "R/B": "ROCKET_BODY", "DEB": "DEBRIS", "UNK": "UNKNOWN"}
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return mapping.get(normalized, normalized or None)
