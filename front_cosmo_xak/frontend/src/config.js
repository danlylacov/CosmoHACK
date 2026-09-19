/** @type {boolean} Switch to false when the real backend is ready. */
export const USE_MOCKS = true;

/** Origin of the mock analyze API. Empty string = same origin (Vite + MSW). */
export const BASE_URL = "";

/** Vite proxy prefix for the live CosmoHACK orbit service. */
export const ORBIT_API_BASE = "/orbit-api";

/** Vite proxy prefix for the live CosmoHACK EVA windows service. */
export const EVA_API_BASE = "/eva-api";

/** Vite proxy prefix for the live space-weather forecast service. */
export const FORECAST_API_BASE = "/forecast-api";

export const EVA_STEP_MIN = 1;
export const EVA_TOP_K = 5;
/** OpenAPI maximum for EVA `critical_distance_km`. */
export const EVA_CRITICAL_MAX_KM = 5;

export const API_PREFIX = "/api/v1";

export const ALGORITHM_VERSION = "eva-risk-0.9.2";

export const HISTORICAL_RANGE = {
  min: "2024-05-01T00:00:00Z",
  max: "2024-06-30T23:59:59Z",
};

export const DURATION_RANGE = { min: 1, max: 8 };
export const PERIOD_RANGE = { min: 1, max: 24 };
export const CRITICAL_DISTANCE_RANGE = { min: 0.1, max: 10000, default: 5 };

/** How often current-mode polls live API /health. */
export const CURRENT_POLL_MS = 30_000;
