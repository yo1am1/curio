/** The evaluation file as the Model quality page tabulates it. */
import type { Evaluation } from "../../api/types";
import { EVALUATED_NAMES } from "./strategies";

export interface QualityRow {
  id: string;
  name: string;
  map: number | null;
  hitRate: number | null;
  recall: number | null;
  vsBaseline: number | null;
  coverage: number | null;
  fill: number | null;
}

/** The strategies' full-holdout results, best MAP@12 first, against the buy-again baseline
 * (or popularity without it). */
export function qualityRows(evaluation: Evaluation): {
  rows: QualityRow[];
  baseline: { id: string; name: string; map: number } | null;
} {
  const results = evaluation.results ?? {};
  const baselineId = "repeat" in results ? "repeat" : "popularity";
  const baseMap = results[baselineId]?.full_holdout.map_at_12 ?? null;
  const rows = Object.entries(results).map(([id, result]) => {
    const full = result.full_holdout;
    const map = full.map_at_12 ?? null;
    return {
      id,
      name: EVALUATED_NAMES[id] ?? id,
      map,
      hitRate: full.hit_rate ?? null,
      recall: full.recall_at_12 ?? null,
      vsBaseline: baseMap && map !== null ? map / baseMap : null,
      coverage: result.catalog_coverage ?? null,
      fill: result.recommendation_fill ?? null,
    };
  });
  rows.sort((a, b) => (b.map ?? 0) - (a.map ?? 0));
  return {
    rows,
    baseline:
      baseMap !== null
        ? { id: baselineId, name: EVALUATED_NAMES[baselineId] ?? baselineId, map: baseMap }
        : null,
  };
}
