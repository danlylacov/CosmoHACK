import { BASE_URL, API_PREFIX, ORBIT_API_BASE, EVA_API_BASE, FORECAST_API_BASE } from "../config.js";

class ApiError extends Error {
  constructor(message, code, payload) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.payload = payload;
  }
}

function errorFromPayload(payload, status) {
  const nested = payload?.error;
  if (nested?.message) return { message: nested.message, code: nested.code || `HTTP_${status}` };
  if (typeof payload?.message === "string") {
    return { message: payload.message, code: payload.code || `HTTP_${status}` };
  }
  const detail = payload?.detail;
  if (typeof detail === "string") return { message: detail, code: `HTTP_${status}` };
  if (Array.isArray(detail) && detail[0]?.msg) {
    return { message: detail.map((d) => d.msg).join("; "), code: detail[0].type || `HTTP_${status}` };
  }
  return { message: `Ошибка запроса (${status})`, code: `HTTP_${status}` };
}

async function requestAt(url, options = {}) {
  const headers = { Accept: "application/json", ...options.headers };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch {
    throw new ApiError("Сеть недоступна. Проверьте соединение и повторите.", "NETWORK", null);
  }

  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new ApiError("Ответ сервера не является JSON.", "INVALID_JSON", null);
    }
  }

  if (!response.ok || payload?.status === "error") {
    const { message, code } = errorFromPayload(payload, response.status);
    throw new ApiError(message, code, payload);
  }

  return payload;
}

async function request(path, options = {}) {
  return requestAt(`${BASE_URL}${API_PREFIX}${path}`, options);
}

export const api = {
  analyze(body) {
    return request("/analyze", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getOrbitPositions(body) {
    return requestAt(`${ORBIT_API_BASE}/api/v1/orbits/positions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getConjunctionDistances(body) {
    return requestAt(`${ORBIT_API_BASE}/api/v1/conjunctions/distances`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getEvaWindows(body) {
    return requestAt(`${EVA_API_BASE}/api/v1/eva/windows`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getForecast(body) {
    return requestAt(`${FORECAST_API_BASE}/forecast`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async getLiveHealth() {
    const probe = async (key, url) => {
      try {
        const response = await fetch(url, { headers: { Accept: "application/json" } });
        return { key, ok: response.ok, status: response.status };
      } catch {
        return { key, ok: false, status: 0 };
      }
    };
    const [orbit, eva, forecast] = await Promise.all([
      probe("orbit", `${ORBIT_API_BASE}/health`),
      probe("eva", `${EVA_API_BASE}/health`),
      probe("forecast", `${FORECAST_API_BASE}/health`),
    ]);
    return { orbit, eva, forecast, checked_at: new Date().toISOString() };
  },

  getTle() {
    return request("/iss/tle");
  },

  getSourcesStatus() {
    return request("/sources/status");
  },

  refreshSources(body = {}) {
    return request("/sources/refresh", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getReport(queryId) {
    return request(`/report/${encodeURIComponent(queryId)}`);
  },
};

export { ApiError };
