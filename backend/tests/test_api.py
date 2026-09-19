from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_calculation_service
from app.core.errors import SourceUnavailableError
from app.main import app


class StubCalculationService:
    def __init__(self, result) -> None:
        self.result = result

    async def calculate(self, _start_time, _end_time):
        return self.result


class FailingCalculationService:
    async def calculate(self, _start_time, _end_time):
        raise SourceUnavailableError("fixture source is offline")


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def install_result(result) -> None:
    app.dependency_overrides[get_calculation_service] = lambda: StubCalculationService(result)


def request_body(result) -> dict[str, str]:
    return {
        "start_time": result.times[0].isoformat().replace("+00:00", "Z"),
        "end_time": (result.times[-1] + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
    }


def test_root_redirects_to_docs(client) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_health_check(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_request_without_critical_threshold(client, result_factory) -> None:
    result = result_factory([8.0, 4.0])
    install_result(result)
    response = client.post("/api/v1/conjunctions/distances", json=request_body(result))
    assert response.status_code == 200
    payload = response.json()
    assert [sample["is_critical"] for sample in payload["samples"]] == [None, None]
    assert payload["critical_intervals"] == []
    assert payload["request"]["critical_distance_km"] is None


def test_request_with_critical_threshold(client, result_factory) -> None:
    result = result_factory([8.0, 4.0, 3.0])
    install_result(result)
    body = {**request_body(result), "critical_distance_km": 5.0}
    response = client.post("/api/v1/conjunctions/distances", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert [sample["is_critical"] for sample in payload["samples"]] == [
        False,
        True,
        True,
    ]
    assert len(payload["critical_intervals"]) == 1


def test_positions_use_samples_and_utc(client, result_factory) -> None:
    result = result_factory([8.0, None])
    install_result(result)
    response = client.post("/api/v1/orbits/positions", json=request_body(result))
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["samples"], list)
    assert payload["samples"][0]["timestamp"].endswith("Z")
    assert payload["samples"][1]["nearest_object"] is None
    assert payload["coordinate_frame"] == "TEME"


def test_period_over_24_hours_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/orbits/positions",
        json={
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-20T00:00:01Z",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_timestamp_without_timezone_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/orbits/positions",
        json={
            "start_time": "2026-09-19T00:00:00",
            "end_time": "2026-09-19T00:01:00",
        },
    )
    assert response.status_code == 422
    errors = response.json()["error"]["details"]["errors"]
    assert any("timezone" in item["message"] for item in errors)


def test_external_source_error_has_service_response(client) -> None:
    app.dependency_overrides[get_calculation_service] = FailingCalculationService
    response = client.post(
        "/api/v1/orbits/positions",
        json={
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:01:00Z",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("threshold", [0, -1])
def test_non_positive_threshold_is_rejected(client, threshold) -> None:
    response = client.post(
        "/api/v1/conjunctions/distances",
        json={
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:01:00Z",
            "critical_distance_km": threshold,
        },
    )
    assert response.status_code == 422
