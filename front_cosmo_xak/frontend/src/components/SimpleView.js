import { queryPanelHTML } from "./QueryForm.js";
import { globeChromeHTML, idleStageHTML } from "../lib/ui.js";
import { formatUtc, formatNumber, escapeHtml } from "../lib/format.js";
import { briefRecommendation } from "../lib/risk.js";
import { describePlanChange } from "../lib/plan.js";

function resultHTML(state) {
  const data = state.data;
  const brief = briefRecommendation(data);
  const bannerStatus = brief.status === "empty" ? "warn" : brief.status;
  const intent = data.request.historical_intent;
  const planNote = describePlanChange(state.previousQuery, state.query, state.previousData, data);
  const windowText = brief.window
    ? `${formatUtc(brief.window.start)} — ${formatUtc(brief.window.end)}`
    : "Окно не выбрано";

  const facts = [];
  if (brief.distance_min_km != null) {
    facts.push(`мин. дистанция ${formatNumber(brief.distance_min_km, 1)} км`);
  }
  const miss = data.risk_mmod?.metrics?.miss_distance;
  if (miss != null && brief.distance_min_km == null) {
    facts.push(`miss ${formatNumber(miss, 1)} км`);
  }

  const caveats = [];
  if (data.status === "partial") caveats.push("Часть источников недоступна.");
  if (brief.caveat) caveats.push(brief.caveat);
  if (data.metadata.sources.some((s) => s.status === "stale")) {
    caveats.push("Есть устаревшие источники — подробности в режиме «Детально».");
  }
  if (intent === "replay") caveats.push("Считаем, как если бы знали только то, что было к началу ВКД.");
  if (intent === "review") caveats.push("Разбор по полному архиву, не прогноз из прошлого.");

  const shout = bannerStatus === "warn" || bannerStatus === "danger";

  return `
    <section class="status-banner status-banner--${bannerStatus}" aria-live="polite">
      <p class="status-banner__label">${escapeHtml(shout ? `${brief.title}!` : brief.title)}</p>
      <p class="status-banner__window">${windowText}</p>
      ${brief.reason ? `<p>${escapeHtml(brief.reason)}</p>` : ""}
      ${facts.length ? `<p>${escapeHtml(facts.join(" · "))}</p>` : ""}
      ${planNote ? `<p class="plan-banner">${escapeHtml(planNote)}</p>` : ""}
      ${caveats.map((c) => `<p class="muted">${escapeHtml(c)}</p>`).join("")}
    </section>
    ${globeChromeHTML(data, {
      simple: true,
      layers: state.layers,
      debrisVisible: state.debrisVisible,
    })}
    <div class="btn-row">
      <a class="btn btn--primary" href="#/advanced">Подробнее</a>
    </div>
  `;
}

export function renderSimpleView(state) {
  const idle = !state.data;
  return `
    <div class="workspace-layout${idle ? " is-idle" : ""}">
      ${queryPanelHTML(state)}
      ${idle ? idleStageHTML(state) : `<div class="panel stack">${resultHTML(state)}</div>`}
    </div>
  `;
}
