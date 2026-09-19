import { formatUtc } from "./format.js";

export function describePlanChange(prevQuery, nextQuery, prevData, nextData) {
  if (!prevQuery || !nextQuery || !prevData || !nextData) return null;
  if (prevQuery.mode !== nextQuery.mode) return null;

  const durationChanged = Number(prevQuery.duration) !== Number(nextQuery.duration);
  const startChanged = prevQuery.start !== nextQuery.start;
  if (!durationChanged && !startChanged) return null;

  const parts = [];
  if (durationChanged) {
    parts.push(
      `Длительность ${prevQuery.duration} ч → ${nextQuery.duration} ч: это смена плана, а не «обстановка стала лучше».`,
    );
  }
  if (startChanged) {
    parts.push(
      `Начало сдвинуто ${formatUtc(prevQuery.start)} → ${formatUtc(nextQuery.start)} при длительности ${nextQuery.duration} ч.`,
    );
  }

  const prevScore = prevData.recommendation?.score;
  const nextScore = nextData.recommendation?.score;
  if (prevScore != null && nextScore != null && durationChanged && nextScore > prevScore) {
    parts.push(
      `Score вырос (${prevScore.toFixed(3)} → ${nextScore.toFixed(3)}), потому что сравниваются разные планы, а не одно и то же окно.`,
    );
  }

  return parts.join(" ");
}
