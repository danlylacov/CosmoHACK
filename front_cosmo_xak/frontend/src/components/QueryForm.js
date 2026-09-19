import { DURATION_RANGE, PERIOD_RANGE, CRITICAL_DISTANCE_RANGE } from "../config.js";
import { isoToDateInput, isoToTimeInput, dateTimeToIso, escapeHtml } from "../lib/format.js";
import { workspaceChromeHTML } from "../lib/ui.js";

export function queryPanelHTML(state) {
  return `
    <aside class="query-panel">
      ${workspaceChromeHTML(state.view)}
      ${queryFormHTML(state.query, state.errors, {
        loadStatus: state.loadStatus,
      })}
    </aside>
  `;
}

export function queryFormHTML(query, errors = {}, extras = {}) {
  const { loadStatus = "idle" } = extras;
  const err = (key) =>
    errors[key] ? `<p class="field-error" id="err-${key}">${escapeHtml(errors[key])}</p>` : "";
  const invalid = (key) => (errors[key] ? "is-invalid" : "");
  const historical = query.mode === "historical";
  const replay = historical && query.historical_intent === "replay";

  return `
    <form class="form-grid" data-form="query" novalidate>
      <fieldset class="field">
        <legend class="field__label">Обстановка</legend>
        <div class="mode-toggle">
          <label>
            <input type="radio" name="mode" value="current" ${query.mode === "current" ? "checked" : ""}>
            Сейчас
          </label>
          <label>
            <input type="radio" name="mode" value="historical" ${historical ? "checked" : ""}>
            Из архива
          </label>
        </div>
        ${err("mode")}
      </fieldset>

      <div class="field">
        <span class="field__label" id="start-label">Начало ВКД</span>
        <div class="utc-row">
          <input id="start_date" name="start_date" type="text" inputmode="numeric" required
            class="${invalid("start")}"
            placeholder="ДД.ММ.ГГГГ"
            value="${escapeHtml(isoToDateInput(query.start))}"
            aria-labelledby="start-label">
          <input id="start_time" name="start_time" type="text" required
            class="${invalid("start")}"
            placeholder="HH:MM"
            pattern="[0-2][0-9]:[0-5][0-9]"
            value="${escapeHtml(isoToTimeInput(query.start))}"
            aria-label="Время начала UTC">
          <abbr class="tz-chip" title="Все метки времени в интерфейсе — UTC">UTC</abbr>
        </div>
        ${err("start")}
      </div>

      <div class="field">
        <label class="field__label" for="duration">Длительность, ч (${DURATION_RANGE.min}–${DURATION_RANGE.max})</label>
        <input id="duration" name="duration" type="number" min="${DURATION_RANGE.min}" max="${DURATION_RANGE.max}" step="1" required
          class="${invalid("duration")}" value="${escapeHtml(query.duration)}">
        ${err("duration")}
      </div>

      <div class="field">
        <label class="field__label" for="period">Искать варианты в пределах, ч (${PERIOD_RANGE.min}–${PERIOD_RANGE.max})</label>
        <input id="period" name="period" type="number" min="${PERIOD_RANGE.min}" max="${PERIOD_RANGE.max}" step="1" required
          class="${invalid("period")}" value="${escapeHtml(query.period)}">
        ${err("period")}
      </div>

      <div class="field">
        <label class="field__label" for="critical_distance_km">Порог сближения MMOD, км</label>
        <input id="critical_distance_km" name="critical_distance_km" type="text" inputmode="decimal" required
          class="${invalid("critical_distance_km")}"
          placeholder="${CRITICAL_DISTANCE_RANGE.default}"
          value="${escapeHtml(query.critical_distance_km ?? CRITICAL_DISTANCE_RANGE.default)}"
          autocomplete="off">
        <p class="field__hint">Введите порог с клавиатуры. Ближе этого расстояния сближение считается критическим (${CRITICAL_DISTANCE_RANGE.min}–${CRITICAL_DISTANCE_RANGE.max} км).</p>
        ${err("critical_distance_km")}
      </div>

      ${historical ? `
        <fieldset class="field">
          <legend class="field__label">Как считать архив</legend>
          <div class="mode-toggle">
            <label>
              <input type="radio" name="historical_intent" value="replay" ${query.historical_intent === "replay" ? "checked" : ""}>
              Как тогда
            </label>
            <label>
              <input type="radio" name="historical_intent" value="review" ${query.historical_intent === "review" ? "checked" : ""}>
              Весь архив
            </label>
          </div>
          <p class="field__hint">
            ${replay
              ? "Только то, что уже было известно к началу ВКД. Поздние факты — отдельно, на оценку не влияют."
              : "Все доступные данные, в том числе после события (постанализ)."}
          </p>
          ${err("historical_intent")}
        </fieldset>
      ` : ""}

      <div class="btn-row btn-row--split">
        <button class="btn btn--primary" type="submit" ${loadStatus === "loading" ? "disabled" : ""}>
          Рассчитать
        </button>
        <button class="btn" type="button" data-action="refresh-sources">Обновить источники</button>
      </div>
    </form>
  `;
}

export function readQueryForm(form, previous = {}) {
  const data = new FormData(form);
  const mode = String(data.get("mode") || "current");
  const start = dateTimeToIso(String(data.get("start_date") || ""), String(data.get("start_time") || ""));
  const historical_intent = mode === "historical" ? String(data.get("historical_intent") || "replay") : null;
  const cutoff_time = historical_intent === "replay" ? start : null;
  return {
    ...previous,
    mode,
    historical_intent,
    start,
    cutoff_time,
    duration: Number(data.get("duration")),
    period: Number(data.get("period")),
    critical_distance_km: Number(String(data.get("critical_distance_km") || "").trim().replace(",", ".")),
  };
}
