import { escapeHtml, formatUtc, formatNumber } from "../lib/format.js";
import { resolveWarningUrl } from "../lib/sources.js";

const TYPE_RU = {
  observation: "наблюдение",
  forecast: "прогноз",
  computed: "расчёт",
};

const MECH_RU = {
  radiation: "радиация",
  mmod: "MMOD",
  illumination: "освещённость",
};

const CONF_RU = {
  low: "низкая",
  medium: "средняя",
  high: "высокая",
};

function formatValue(warning) {
  if (warning.value == null) return "—";
  const digits = warning.value < 1 && warning.value > 0 ? 4 : 2;
  return `${formatNumber(warning.value, digits)}${warning.unit ? ` ${warning.unit}` : ""}`;
}

function sourceHTML(warning, sources) {
  const href = resolveWarningUrl(warning, sources);
  if (href) {
    return `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${escapeHtml(warning.source)}</a>`;
  }
  return escapeHtml(warning.source || "—");
}

function warningItemHTML(warning, sources, later = false) {
  const period = warning.period
    ? `${formatUtc(warning.period.start)} — ${formatUtc(warning.period.end)}`
    : "—";
  const kicker = [
    later ? "после отсечки" : null,
    TYPE_RU[warning.type] || warning.type,
    MECH_RU[warning.mechanism] || warning.mechanism,
    warning.confidence ? `уверенность ${CONF_RU[warning.confidence] || warning.confidence}` : null,
    warning.intersects_window ? "пересекает окно ВКД" : "вне окна",
    later ? "в оценку не входило" : null,
  ].filter(Boolean);

  return `
    <article class="warning-item">
      <p class="warning-item__kicker">${escapeHtml(kicker.join(" · "))}</p>
      <h3>${escapeHtml(warning.title)}</h3>
      <dl class="kv">
        <dt>Событие UTC</dt>
        <dd>${escapeHtml(formatUtc(warning.event_time))}</dd>
        <dt>Период</dt>
        <dd>${escapeHtml(period)}</dd>
        <dt>Опубликовано</dt>
        <dd>${escapeHtml(formatUtc(warning.published_at))}</dd>
        <dt>Значение</dt>
        <dd>${escapeHtml(formatValue(warning))}</dd>
        <dt>Влияние</dt>
        <dd>${escapeHtml(warning.impact || "—")}</dd>
        <dt>Правило</dt>
        <dd class="mono">${escapeHtml(warning.rule || "—")}</dd>
        <dt>Ограничения</dt>
        <dd>${escapeHtml(warning.limitations || "—")}</dd>
        <dt>Источник</dt>
        <dd>${sourceHTML(warning, sources)}</dd>
      </dl>
    </article>
  `;
}

export function warningsListHTML(warnings, sources = [], later = false) {
  if (!warnings.length) {
    return `<p class="muted">Нет предупреждений для отображения.</p>`;
  }

  return `<div class="warning-list">${warnings.map((w) => warningItemHTML(w, sources, later)).join("")}</div>`;
}
