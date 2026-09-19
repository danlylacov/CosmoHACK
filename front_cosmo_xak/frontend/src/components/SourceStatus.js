import { escapeHtml, formatUtc, pct } from "../lib/format.js";

function statusRu(s, disabled) {
  if (disabled) return "выкл";
  if (s.status === "ok") return "ок";
  if (s.status === "stale") return "устарел";
  if (s.status === "error") return "ошибка";
  return s.status;
}

function statusClass(s, disabled) {
  if (disabled) return "is-off";
  if (s.status === "stale") return "is-stale";
  if (s.status === "error") return "is-error";
  return "";
}

function sourceRowHTML(s, sourceControls) {
  const flags = sourceControls[s.name] || {};
  const disabled = flags.disabled ?? s.enabled === false;
  const frozen = flags.frozen ?? s.frozen === true;
  const extra = s.unsuitable_for_replay ? "нет метки публикации в архиве" : "";
  return `
    <tr>
      <td class="cell-wrap">
        <a href="${escapeHtml(s.url)}" target="_blank" rel="noreferrer">${escapeHtml(s.name)}</a>
        <div class="cell-sub">получено ${escapeHtml(formatUtc(s.fetched_at))}</div>
        ${extra ? `<div class="cell-sub">${escapeHtml(extra)}</div>` : ""}
        <div class="source-toggles">
          <label>
            <input type="checkbox" data-source-flag="disabled" data-source-name="${escapeHtml(s.name)}" ${disabled ? "checked" : ""}>
            отключить
          </label>
          <label>
            <input type="checkbox" data-source-flag="frozen" data-source-name="${escapeHtml(s.name)}" ${frozen ? "checked" : ""}>
            заморозить
          </label>
        </div>
      </td>
      <td class="${statusClass(s, disabled)}">
        ${escapeHtml(statusRu(s, disabled))}
        <div class="cell-sub">${escapeHtml(s.age)}</div>
      </td>
    </tr>
  `;
}

function sourceTableHTML(sources, sourceControls) {
  if (!sources.length) return "";
  const rows = sources.map((s) => sourceRowHTML(s, sourceControls)).join("");
  return `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th>Источник</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

export function sourceStatusHTML(metadata, { showRefresh = true, sourceControls = {} } = {}) {
  if (!metadata?.sources?.length) {
    return `<p class="muted">Нет данных для отображения</p>`;
  }

  return `
    <div class="stack">
      <p class="muted source-summary">
        Полнота <span class="mono">${pct(metadata.completeness)}</span>
        · уверенность SW <span class="mono">${escapeHtml(metadata.confidence_sw)}</span>
        · MMOD <span class="mono">${escapeHtml(metadata.confidence_mmod)}</span>
      </p>
      <p class="field__hint">Отключение источника не делает риск нулевым: по этому механизму оценка становится «недостаточно данных».</p>
      <div class="source-grid">
        ${sourceTableHTML(metadata.sources, sourceControls)}
      </div>
      ${showRefresh ? `<div class="btn-row"><button class="btn" type="button" data-action="refresh-sources">Обновить незамороженные</button></div>` : ""}
    </div>
  `;
}
