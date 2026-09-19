import { formatUtc, formatNumber, pct } from "../lib/format.js";

function allowedLabel(w) {
  if (w.eva_allowed === true) return "разрешён";
  if (w.eva_allowed === false) return "запрещён";
  return "—";
}

export function windowComparisonHTML(data, planNote = null) {
  if (!data?.windows?.length) {
    return `<p class="muted">Нет данных для отображения</p>`;
  }

  const rec = data.recommendation?.window;
  const recKey = rec ? `${rec.start}|${rec.end}` : null;
  const noChoice = !rec;
  const ranked = data.windows.slice();
  const top = ranked[0];
  const tied = ranked.filter(
    (w) =>
      top &&
      Boolean(w.contains_conjunction) === Boolean(top.contains_conjunction) &&
      Math.abs((w.score ?? 0) - (top.score ?? 0)) < 0.01,
  );

  const rows = ranked
    .map((w) => {
      const key = `${w.start}|${w.end}`;
      const winner = recKey && key === recKey;
      const isTie = tied.length > 1 && tied.some((t) => `${t.start}|${t.end}` === key);
      return `
        <tr class="${winner ? "is-winner" : ""} ${isTie ? "is-tie" : ""} ${w.requires_check ? "is-check" : ""}">
          <td data-label="Начало UTC">${formatUtc(w.start)}</td>
          <td data-label="Конец UTC">${formatUtc(w.end)}</td>
          <td data-label="Оценка">${formatNumber(w.score, 3)}</td>
          <td data-label="SW">${formatNumber(w.risk_sw, 3)}</td>
          <td data-label="MMOD">${formatNumber(w.risk_mmod, 3)}</td>
          <td data-label="Выход">${allowedLabel(w)}</td>
          <td data-label="Пересечение SW, ч">${formatNumber(w.overlap_sw, 2)}</td>
          <td data-label="Пересечение MMOD, ч">${formatNumber(w.overlap_mmod, 2)}</td>
          <td data-label="Полнота">${pct(w.completeness)}</td>
          <td data-label="Проверка">${w.requires_check ? "да" : "—"}</td>
        </tr>
      `;
    })
    .join("");

  const banners = [];
  if (planNote) banners.push(`<p class="plan-banner">${planNote}</p>`);
  if (noChoice) {
    banners.push(
      `<p class="muted">Недостаточно оснований для выбора. Требуется дополнительная проверка.</p>`,
    );
  }
  if (data.recommendation?.tie || tied.length > 1) {
    banners.push(
      `<p class="muted">Равнозначные варианты по score. Компромисс не сводится к одному механизму.</p>`,
    );
  }

  return `
    <div class="stack">
      ${banners.join("")}
      <p class="muted">Лучшее окно подсвечено. В колонке «проверка» — «да», если нужна дополнительная оценка.</p>
      <div class="table-wrap">
        <table class="data data--windows">
          <thead>
            <tr>
              <th>Начало UTC</th>
              <th>Конец UTC</th>
              <th>Оценка</th>
              <th>SW</th>
              <th>MMOD</th>
              <th>Выход</th>
              <th>Пересечение SW, ч</th>
              <th>Пересечение MMOD, ч</th>
              <th>Полнота</th>
              <th>Проверка</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}
