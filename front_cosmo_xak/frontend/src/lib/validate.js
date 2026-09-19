import { DURATION_RANGE, PERIOD_RANGE, HISTORICAL_RANGE, CRITICAL_DISTANCE_RANGE } from "../config.js";

export function validateQuery(query) {
  const errors = {};
  const duration = Number(query.duration);
  const period = Number(query.period);

  if (!query.mode || !["current", "historical"].includes(query.mode)) {
    errors.mode = "Выберите режим: текущая обстановка или историческая дата.";
  }

  if (query.mode === "historical" && !["review", "replay"].includes(query.historical_intent)) {
    errors.historical_intent =
      "Для historical выберите разбор архива либо прогноз из прошлого (replay).";
  }

  if (!query.start) {
    errors.start = "Укажите дату и время начала ВКД (UTC), например 15.06.2024.";
  } else if (Number.isNaN(Date.parse(query.start))) {
    errors.start = "Некорректная дата. Допустимо ДД.ММ.ГГГГ, ДД-ММ-ГГГГ или ДД/ММ/ГГГГ.";
  } else if (query.mode === "historical") {
    const t = Date.parse(query.start);
    const min = Date.parse(HISTORICAL_RANGE.min);
    const max = Date.parse(HISTORICAL_RANGE.max);
    if (t < min || t > max) {
      errors.start = "Для архива дата должна быть в диапазоне 01.05.2024 … 30.06.2024 UTC.";
    }
  }

  if (query.mode === "historical" && query.historical_intent === "replay") {
    if (!query.cutoff_time) {
      errors.cutoff_time = "Для replay укажите момент отсечения публикации (UTC).";
    } else if (Number.isNaN(Date.parse(query.cutoff_time))) {
      errors.cutoff_time = "cutoff_time должен быть ISO 8601 UTC.";
    } else if (Date.parse(query.cutoff_time) > Date.parse(query.start || 0)) {
      errors.cutoff_time = "Отсечение не может быть позже начала рассматриваемого окна.";
    }
  }

  if (!Number.isFinite(duration) || duration < DURATION_RANGE.min || duration > DURATION_RANGE.max) {
    errors.duration = `Длительность ВКД — целое число от ${DURATION_RANGE.min} до ${DURATION_RANGE.max} ч.`;
  }

  if (!Number.isFinite(period) || period < PERIOD_RANGE.min || period > PERIOD_RANGE.max) {
    errors.period = `Период поиска — целое число от ${PERIOD_RANGE.min} до ${PERIOD_RANGE.max} ч.`;
  }

  if (Number.isFinite(duration) && Number.isFinite(period) && duration > period) {
    errors.period = "Период поиска не может быть короче длительности ВКД.";
  }

  const critical = Number(query.critical_distance_km);
  if (!Number.isFinite(critical) || critical < CRITICAL_DISTANCE_RANGE.min || critical > CRITICAL_DISTANCE_RANGE.max) {
    errors.critical_distance_km = `Порог сближения — число от ${CRITICAL_DISTANCE_RANGE.min} до ${CRITICAL_DISTANCE_RANGE.max} км.`;
  }

  return errors;
}

export function defaultQuery(mode = "historical") {
  if (mode === "historical") {
    return {
      mode,
      historical_intent: "replay",
      start: "2024-06-15T08:00:00Z",
      cutoff_time: "2024-06-15T08:00:00Z",
      duration: 6,
      period: 12,
      critical_distance_km: CRITICAL_DISTANCE_RANGE.default,
      disabled_sources: [],
      frozen_sources: [],
    };
  }
  const now = new Date();
  now.setUTCMinutes(0, 0, 0);
  const start = now.toISOString().replace(/\.\d{3}Z$/, "Z");
  return {
    mode: "current",
    historical_intent: null,
    start,
    cutoff_time: null,
    duration: 6,
    period: 12,
    critical_distance_km: CRITICAL_DISTANCE_RANGE.default,
    disabled_sources: [],
    frozen_sources: [],
  };
}

export function toAnalyzeRequest(query) {
  const historical = query.mode === "historical";
  return {
    mode: query.mode,
    start: query.start,
    duration: Number(query.duration),
    period: Number(query.period),
    historical_intent: historical ? query.historical_intent || "replay" : null,
    cutoff_time: historical && query.historical_intent === "replay" ? query.cutoff_time : null,
    disabled_sources: query.disabled_sources ?? [],
    frozen_sources: query.frozen_sources ?? [],
  };
}
