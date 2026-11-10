import { ArrowLeft } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { Link } from "react-router";

import { useEvaluation } from "../../api/queries";
import type { Evaluation } from "../../api/types";
import { DOMAIN_PAGES } from "../../domains/registry";
import { Disclosure } from "../../ui/Disclosure";
import { ErrorMessage, StateMessage } from "../../ui/StateMessage";
import { Shimmer } from "../../ui/Skeleton";
import { useWidth } from "../../ui/useWidth";
import browse from "../browse/Browse.module.css";
import { PageHeader } from "../shell/PageHeader";
import { type QualityRow, qualityRows } from "./quality";
import styles from "./Quality.module.css";

/** A round tick step (1, 2 or 5 × a power of ten) near `raw`. */
function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  return (unit < 1.5 ? 1 : unit < 3.5 ? 2 : 5) * power;
}

const pct = (value: number | null | undefined, digits = 1) =>
  value == null ? "Not recorded" : `${(value * 100).toFixed(digits)}%`;
const map4 = (value: number | null | undefined) => (value == null ? "—" : value.toFixed(4));
const signed = (value: number) => `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(4)}`;

/** Full-holdout MAP@12 per strategy as horizontal bars, with the baseline as a dashed rule. */
function MapChart({
  rows,
  baseline,
}: {
  rows: QualityRow[];
  baseline: { id: string; name: string; map: number } | null;
}) {
  const [box, width] = useWidth<HTMLDivElement>(720);
  const [hover, setHover] = useState<string | null>(null);
  const bars = rows.filter((row) => row.map !== null);
  const compact = width < 560;
  const labelW = compact ? 0 : 190;
  const valueW = 64;
  const rowH = compact ? 50 : 40;
  const top = compact ? 4 : 6;
  const plotW = Math.max(120, width - labelW - valueW - 8);
  const top_ = Math.max(...bars.map((r) => r.map ?? 0), baseline?.map ?? 0) || 1;
  const step = niceStep(top_ / 4);
  const max = Math.ceil((top_ * 1.02) / step) * step;
  const x = (value: number) => labelW + (value / max) * plotW;
  const height = top + bars.length * rowH + 30;
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const barH = 14;
  const barY = (index: number) => top + index * rowH + (compact ? 24 : (rowH - barH) / 2);

  return (
    <figure className={styles.figure}>
      <div ref={box} className={styles.chart}>
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${String(width)} ${String(height)}`}
          role="img"
          aria-label={`MAP@12 by strategy on all held-out purchases${baseline ? `; dashed rule: ${baseline.name}` : ""}. The table below lists every value.`}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line className={styles.grid} x1={x(tick)} x2={x(tick)} y1={top} y2={height - 24} />
              <text
                className={styles.tick}
                x={x(tick)}
                y={height - 8}
                textAnchor={tick === 0 && labelW === 0 ? "start" : "middle"}
              >
                {tick.toFixed(step < 0.01 ? 3 : 2)}
              </text>
            </g>
          ))}
          {bars.map((row, index) => {
            const y = barY(index);
            const w = Math.max(2, x(row.map ?? 0) - labelW);
            const dim = hover !== null && hover !== row.id;
            return (
              <g
                key={row.id}
                onMouseEnter={() => {
                  setHover(row.id);
                }}
                onMouseLeave={() => {
                  setHover(null);
                }}
                opacity={dim ? 0.45 : 1}
                className={styles.barRow}
              >
                <rect
                  className={styles.hitArea}
                  x={0}
                  y={top + index * rowH}
                  width={width}
                  height={rowH}
                />
                <text
                  className={styles.name}
                  x={compact ? 0 : labelW - 12}
                  y={compact ? y - 7 : y + barH / 2 + 4}
                  textAnchor={compact ? "start" : "end"}
                >
                  {row.name}
                </text>
                <motion.rect
                  className={styles.bar}
                  data-baseline={row.id === baseline?.id ? "true" : undefined}
                  x={labelW}
                  y={y}
                  height={barH}
                  rx={4}
                  initial={{ width: 0 }}
                  animate={{ width: w }}
                  transition={{ duration: 0.6, delay: index * 0.05, ease: [0.22, 1, 0.36, 1] }}
                />
                <text className={styles.value} x={labelW + w + 6} y={y + barH / 2 + 4}>
                  {map4(row.map)}
                </text>
              </g>
            );
          })}
          {baseline ? (
            <line
              className={styles.rule}
              x1={x(baseline.map)}
              x2={x(baseline.map)}
              y1={top - 2}
              y2={height - 24}
            />
          ) : null}
        </svg>
        {hover
          ? (() => {
              const index = bars.findIndex((r) => r.id === hover);
              const row = bars[index];
              if (!row) return null;
              return (
                <div
                  className={styles.tooltip}
                  style={{
                    left: Math.min(x(row.map ?? 0) + 8, width - 200),
                    top: barY(index) - 8,
                  }}
                >
                  <strong>{row.name}</strong>
                  <span>MAP@12 {map4(row.map)}</span>
                  <span>
                    Hit rate {pct(row.hitRate)} · Recall {pct(row.recall)}
                  </span>
                  {row.vsBaseline !== null ? (
                    <span>{row.vsBaseline.toFixed(2)}× the baseline</span>
                  ) : null}
                </div>
              );
            })()
          : null}
      </div>
      <figcaption className={styles.caption}>
        MAP@12 on all held-out purchases.
        {baseline ? (
          <>
            {" "}
            <span className={styles.ruleKey} aria-hidden="true" /> Dashed rule: {baseline.name}.
          </>
        ) : null}{" "}
        Repeat purchases are included for every strategy.
      </figcaption>
    </figure>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.metric}>
      <dt>{label}</dt>
      <dd className="tabular">{value}</dd>
    </div>
  );
}

function Report({ evaluation }: { evaluation: Evaluation }) {
  const { rows, baseline } = qualityRows(evaluation);
  const scope = evaluation.sample_scope;
  const best = rows[0];
  const gain = evaluation.learned_vs_repeat;
  const index = evaluation.content_index;
  if (!best) {
    return <StateMessage>No evaluated strategies are available yet.</StateMessage>;
  }
  return (
    <>
      <p className={styles.scope}>
        {evaluation.evaluation_role === "validation" ? (
          <span className={styles.role}>Earlier validation week; not the final holdout.</span>
        ) : null}
        <strong>{(evaluation.users_evaluated ?? 0).toLocaleString("en-US")} customers</strong>
        {evaluation.cutoff ? ` · ${evaluation.cutoff} to ${evaluation.holdout_end ?? "?"}` : ""}
        {evaluation.top_k ? ` · ${String(evaluation.top_k)} recommendations each` : ""}
      </p>
      <p className={browse.caption}>
        Prepared catalog and customer selection may favor every strategy; these are sample-level
        results, not population or leaderboard estimates.
      </p>
      <section className={styles.best} aria-labelledby="best-title">
        <p className={styles.kicker}>Highest full-holdout MAP in this measured sample</p>
        <h2 id="best-title" className={styles.bestName}>
          {best.name}
        </h2>
        <dl className={styles.metrics}>
          <Metric label="Customers reached" value={pct(best.hitRate)} />
          <Metric label="Purchased items recovered" value={pct(best.recall)} />
          <Metric label="Ranking quality · MAP@12" value={map4(best.map)} />
        </dl>
        {gain ? (
          <p className={browse.caption}>
            Observed MAP gain vs buy-again: {signed(gain.mean_delta)}. 95% paired-customer interval:{" "}
            {signed(gain.lower_95)} to {signed(gain.upper_95)}.{" "}
            {gain.lower_95 <= 0 && gain.upper_95 >= 0
              ? "The interval includes zero; the single-week advantage remains uncertain."
              : "Interval covers selected-customer uncertainty only."}
          </p>
        ) : null}
      </section>
      <MapChart rows={rows} baseline={baseline} />
      {/* Scrolls sideways on a phone, so the keyboard must be able to reach it. */}
      <div
        className={styles.tableWrap}
        role="region"
        aria-label="Every strategy's results"
        tabIndex={0}
      >
        <table className={styles.table}>
          <caption className="visually-hidden">Every strategy's results</caption>
          <thead>
            <tr>
              <th scope="col">Strategy</th>
              <th scope="col">MAP@12</th>
              <th scope="col">Hit rate</th>
              <th scope="col">Recall@12</th>
              <th scope="col">vs baseline</th>
              <th scope="col">Catalog coverage</th>
              <th scope="col">List fill</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <th scope="row">{row.name}</th>
                <td>{map4(row.map)}</td>
                <td>{pct(row.hitRate)}</td>
                <td>{pct(row.recall)}</td>
                <td>{row.vsBaseline === null ? "—" : `${row.vsBaseline.toFixed(2)}×`}</td>
                <td>{pct(row.coverage)}</td>
                <td>{pct(row.fill)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={browse.caption}>
        Hit rate: customers with at least one matching recommendation. Recall: average share of each
        customer&apos;s purchased items recovered. MAP@12 measures ranking quality, not a
        probability of buying.
      </p>
      {index == null ? (
        <p className={browse.caption}>
          Content-based results are missing: the evaluation ran without an image index.
        </p>
      ) : scope?.catalog_products && index.indexed_products < scope.catalog_products ? (
        <p className={browse.caption}>
          Visual-style results use {index.indexed_products.toLocaleString("en-US")} of{" "}
          {scope.catalog_products.toLocaleString("en-US")} product photos. This partial index limits
          coverage.
        </p>
      ) : null}
      <Disclosure summary="Data, protocol and limits">
        <div className={styles.protocol}>
          {scope ? (
            <p>
              The {(scope.prepared_users ?? 0).toLocaleString("en-US")}-customer sample contains{" "}
              {(scope.catalog_products ?? 0).toLocaleString("en-US")} products. Every
              purchase-derived signal is trained strictly before the holdout. Cohort and catalog
              selection may use the full window; results describe this sample, not the population or
              a competition leaderboard.
            </p>
          ) : null}
          <p>
            Novel-item metrics use the original ranked lists and are diagnostic. A repeat strategy
            and a discovery strategy solve different tasks.
          </p>
          {evaluation.repeat_purchase_limitation ? (
            <pre className={styles.json}>
              {JSON.stringify(evaluation.repeat_purchase_limitation, null, 2)}
            </pre>
          ) : null}
        </div>
      </Disclosure>
    </>
  );
}

/** Model quality: how each feed strategy scored against what people bought next. Outside the
 * navigation, linked from For you. */
export function QualityPage() {
  const evaluation = useEvaluation();
  return (
    <div className={browse.page}>
      <Link to={DOMAIN_PAGES.fashion[0]?.to ?? "/fashion"} className={styles.back}>
        <ArrowLeft size={15} aria-hidden="true" /> For you
      </Link>
      <PageHeader
        title="Model quality"
        documentTitle="Model quality · Fashion"
        intro="Measured against what people bought next."
      />
      {evaluation.isPending ? (
        <div aria-busy="true" aria-label="Loading the evaluation" className={styles.loading}>
          <Shimmer height="6rem" />
          <Shimmer height="16rem" />
        </div>
      ) : evaluation.error ? (
        <ErrorMessage error={evaluation.error} />
      ) : (
        <Report evaluation={evaluation.data} />
      )}
    </div>
  );
}
