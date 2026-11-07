import { AnimatePresence, motion } from "motion/react";
import { useId, useState, type KeyboardEvent } from "react";

import { productImageUrl } from "../../api/client";
import { useWidth } from "../../ui/useWidth";
import styles from "./Connections.module.css";
import {
  KIND_LABELS,
  RELATION_LABELS,
  type GraphLink,
  type GraphPoint,
  type GraphView,
  truncate,
  weightText,
} from "./graph";

interface Layout {
  width: number;
  height: number;
  cx: number;
  cy: number;
  radius: number;
  focusR: number;
  productR: number;
  dotR: number;
  label: number;
  compact: boolean;
}

function layoutFor(width: number): Layout {
  const compact = width < 560;
  const height = compact ? Math.round(Math.min(width * 1.08, 460)) : 540;
  const padX = compact ? 50 : 130;
  const padY = compact ? 58 : 66;
  return {
    width,
    height,
    cx: width / 2,
    cy: height / 2,
    radius: Math.max(80, Math.min(width / 2 - padX, height / 2 - padY)),
    focusR: compact ? 32 : 44,
    productR: compact ? 21 : 28,
    dotR: compact ? 6 : 7,
    label: compact ? 14 : 24,
    compact,
  };
}

const at = (layout: Layout, point: GraphPoint) => ({
  x: layout.cx + point.x * layout.radius,
  y: layout.cy + point.y * layout.radius,
});

function nodeRadius(layout: Layout, point: GraphPoint): number {
  if (point.focus) return layout.focusR;
  return point.kind === "product" ? layout.productR : layout.dotR;
}

/** The edge between the focus and `point` (the view is a star around the focus). */
function linkOf(view: GraphView, point: GraphPoint): GraphLink | undefined {
  return view.links.find((link) => link.source === point.id || link.target === point.id);
}

function describe(view: GraphView, point: GraphPoint): string {
  const link = point.focus ? undefined : linkOf(view, point);
  const kind = point.focus ? "Starting piece" : KIND_LABELS[point.kind];
  const relation = link ? ` · ${RELATION_LABELS[link.relation]}, ${weightText(link)}` : "";
  const action = point.productId !== null ? ". Open the product" : "";
  return `${point.label} (${kind})${relation}${action}`;
}

/** Where a small node's label goes: beside it, away from the centre. */
function labelAnchor(point: GraphPoint): {
  anchor: "start" | "middle" | "end";
  dx: number;
  dy: number;
} {
  if (point.x > 0.35) return { anchor: "start", dx: 1, dy: 0 };
  if (point.x < -0.35) return { anchor: "end", dx: -1, dy: 0 };
  return { anchor: "middle", dx: 0, dy: point.y < 0 ? -1 : 1 };
}

/**
 * The product neighborhood as an interactive radial drawing in SVG: product photos as round
 * nodes, attributes and categories as coloured dots, the focus in the middle. Hover or focus a
 * node for its relationship; select a product to open it.
 */
export function ConnectionsGraph({
  view,
  selected,
  onSelect,
  onOpen,
}: {
  view: GraphView;
  selected: string | null;
  onSelect: (point: GraphPoint) => void;
  onOpen: (productId: number) => void;
}) {
  const [box, width] = useWidth<HTMLDivElement>(720);
  const [hovered, setHovered] = useState<string | null>(null);
  const [broken, setBroken] = useState<ReadonlySet<string>>(new Set());
  const clipBase = useId().replace(/:/g, "");
  const layout = layoutFor(width);
  const focus = view.focus;
  if (!focus) return null;
  const center = at(layout, focus);
  const active = hovered ?? selected;
  const hoveredPoint = view.points.find((p) => p.id === hovered) ?? null;

  function activate(point: GraphPoint) {
    onSelect(point);
    if (point.productId !== null) onOpen(point.productId);
  }

  function onKey(event: KeyboardEvent, point: GraphPoint) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(point);
    }
  }

  const neighbors = view.points.filter((p) => !p.focus);
  return (
    <div ref={box} className={styles.canvas} style={{ height: layout.height }}>
      <svg
        className={styles.svg}
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${String(layout.width)} ${String(layout.height)}`}
        role="group"
        aria-label={`Connections of ${focus.label}`}
      >
        <defs>
          {view.points
            .filter((p) => p.kind === "product")
            .map((p) => (
              <clipPath key={p.id} id={`${clipBase}-${p.id.replace(/\W/g, "_")}`}>
                <circle r={nodeRadius(layout, p) - 2.5} />
              </clipPath>
            ))}
        </defs>
        <circle
          className={styles.orbit}
          cx={center.x}
          cy={center.y}
          r={layout.radius}
          fill="none"
        />
        <g>
          {neighbors.map((point, index) => {
            const end = at(layout, point);
            const link = linkOf(view, point);
            const dim = active !== null && active !== point.id && active !== focus.id;
            return (
              <motion.line
                key={point.id}
                className={styles.edge}
                data-relation={link?.relation}
                data-active={active === point.id ? "true" : undefined}
                x1={center.x}
                y1={center.y}
                initial={{ x2: center.x, y2: center.y, opacity: 0 }}
                animate={{ x2: end.x, y2: end.y, opacity: dim ? 0.18 : 1 }}
                transition={{ duration: 0.5, delay: index * 0.03, ease: [0.22, 1, 0.36, 1] }}
              />
            );
          })}
        </g>
        {[...neighbors, focus].map((point, index) => {
          const pos = at(layout, point);
          const r = nodeRadius(layout, point);
          const isProduct = point.kind === "product";
          const dim = active !== null && active !== point.id && !point.focus;
          const place = labelAnchor(point);
          const title = truncate(point.title, point.focus ? 28 : layout.label);
          const labelY = isProduct
            ? r + (layout.compact ? 13 : 16)
            : place.dy * (r + 13) + (place.dy === 0 ? 4 : 0);
          const labelX = isProduct ? 0 : place.dx * (r + 7);
          const photoOk = isProduct && point.productId !== null && !broken.has(point.id);
          return (
            <motion.g
              key={point.id}
              className={styles.node}
              data-kind={point.kind}
              data-focus={point.focus ? "true" : undefined}
              data-selected={selected === point.id ? "true" : undefined}
              data-testid="graph-node"
              role="button"
              tabIndex={0}
              aria-label={describe(view, point)}
              initial={{ x: center.x, y: center.y, opacity: 0, scale: 0.4 }}
              animate={{ x: pos.x, y: pos.y, opacity: dim ? 0.45 : 1, scale: 1 }}
              transition={{
                type: "spring",
                stiffness: 260,
                damping: 26,
                delay: point.focus ? 0 : 0.05 + index * 0.03,
              }}
              onMouseEnter={() => {
                setHovered(point.id);
              }}
              onMouseLeave={() => {
                setHovered(null);
              }}
              onFocus={() => {
                setHovered(point.id);
              }}
              onBlur={() => {
                setHovered(null);
              }}
              onClick={() => {
                activate(point);
              }}
              onKeyDown={(event) => {
                onKey(event, point);
              }}
            >
              <circle className={styles.hit} r={Math.max(r, 16) + 6} />
              <circle className={styles.ring} r={r} />
              {photoOk ? (
                <image
                  href={productImageUrl(point.productId ?? 0)}
                  x={-r}
                  y={-r * 1.25}
                  width={r * 2}
                  height={r * 2.5}
                  preserveAspectRatio="xMidYMid slice"
                  clipPath={`url(#${clipBase}-${point.id.replace(/\W/g, "_")})`}
                  onError={() => {
                    setBroken((current) => new Set(current).add(point.id));
                  }}
                />
              ) : null}
              <text
                className={styles.label}
                x={labelX}
                y={labelY}
                textAnchor={isProduct ? "middle" : place.anchor}
                data-focus={point.focus ? "true" : undefined}
              >
                {title}
              </text>
              {point.caption && !layout.compact ? (
                <text
                  className={styles.caption}
                  x={labelX}
                  y={labelY + (place.dy < 0 ? -15 : 15)}
                  textAnchor={isProduct ? "middle" : place.anchor}
                >
                  {point.caption}
                </text>
              ) : null}
            </motion.g>
          );
        })}
      </svg>
      <AnimatePresence>
        {hoveredPoint ? (
          <Tooltip key={hoveredPoint.id} view={view} point={hoveredPoint} layout={layout} />
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function Tooltip({ view, point, layout }: { view: GraphView; point: GraphPoint; layout: Layout }) {
  const pos = at(layout, point);
  const link = point.focus ? undefined : linkOf(view, point);
  const below = pos.y < layout.height * 0.38;
  const r = nodeRadius(layout, point);
  const left = Math.max(110, Math.min(layout.width - 110, pos.x));
  return (
    <motion.div
      className={styles.tooltip}
      role="presentation"
      style={{
        left,
        top: below ? pos.y + r + 30 : pos.y - r - 12,
        translate: below ? "-50% 0" : "-50% -100%",
      }}
      initial={{ opacity: 0, y: below ? -4 : 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.12 }}
    >
      <span className={styles.tooltipKind} data-kind={point.kind}>
        {point.focus ? "Starting piece" : KIND_LABELS[point.kind]}
      </span>
      <strong className={styles.tooltipTitle}>{point.label}</strong>
      {link ? (
        <span className={styles.tooltipMeta}>
          {RELATION_LABELS[link.relation]} · {weightText(link)}
        </span>
      ) : null}
      {point.productId !== null ? (
        <span className={styles.tooltipHint}>Select to open the product</span>
      ) : null}
    </motion.div>
  );
}

/** The node colours, for the legend under the drawing. */
export function GraphLegend() {
  return (
    <ul className={styles.legend} aria-label="Node types">
      {(["product", "attribute", "taxonomy"] as const).map((kind) => (
        <li key={kind}>
          <span className={styles.swatch} data-kind={kind} aria-hidden="true" />
          {KIND_LABELS[kind]}
        </li>
      ))}
      <li>
        <span className={styles.line} data-relation="co_purchased" aria-hidden="true" />
        Bought together
      </li>
      <li>
        <span className={styles.line} data-relation="has_attribute" aria-hidden="true" />
        Shared attribute or category
      </li>
    </ul>
  );
}
