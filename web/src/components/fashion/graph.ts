/**
 * A product neighborhood (`/products/{id}/graph`) as a bounded one-hop view, as the Streamlit
 * client draws it: the focus product in the middle, at most 12 nodes and 24 relationships, the
 * strongest first, with stable positions on a circle. Pure, so the drawing is easy to test.
 */
import type { GraphEdge, GraphNode, ProductGraph } from "../../api/types";

export const MAX_NODES = 12;
export const MAX_LINKS = 24;

export type NodeKind = GraphNode["kind"];
export type Relation = GraphEdge["relation"];

export interface GraphPoint {
  id: string;
  kind: NodeKind;
  productId: number | null;
  focus: boolean;
  /** The node's full label from the API. */
  label: string;
  /** What the drawing shows: a category's last level, an attribute's value. */
  title: string;
  /** An attribute's name ("pattern"), shown under its value. */
  caption: string | null;
  /** Unit-circle position (the focus sits at 0, 0); the drawing scales it. */
  x: number;
  y: number;
}

export interface GraphLink {
  source: string;
  target: string;
  relation: Relation;
  weight: number;
  sourceLabel: string;
  targetLabel: string;
}

export interface GraphView {
  focus: GraphPoint | null;
  points: GraphPoint[];
  links: GraphLink[];
}

const KIND_ORDER: Record<NodeKind, number> = { product: 0, attribute: 1, taxonomy: 2 };

export const KIND_LABELS: Record<NodeKind, string> = {
  product: "Product",
  attribute: "Attribute",
  taxonomy: "Category",
};

export const RELATION_LABELS: Record<Relation, string> = {
  co_purchased: "Bought together",
  has_attribute: "Has attribute",
  in_category: "In category",
};

/** A label for the drawing: categories by their last level, attributes by their value. */
export function displayLabel(node: Pick<GraphNode, "kind" | "label">): {
  title: string;
  caption: string | null;
} {
  if (node.kind === "taxonomy") {
    const levels = node.label.split(">").map((level) => level.trim());
    return { title: levels[levels.length - 1] ?? node.label, caption: "category" };
  }
  if (node.kind === "attribute") {
    const at = node.label.indexOf(":");
    if (at > 0) {
      return {
        title: node.label.slice(at + 1).trim(),
        caption: node.label.slice(0, at).trim().replace(/-/g, " "),
      };
    }
  }
  return { title: node.label, caption: null };
}

/** What a relationship's weight means, in words. */
export function weightText(link: Pick<GraphLink, "relation" | "weight">): string {
  if (link.relation === "co_purchased") return `Lift ${link.weight.toFixed(1)}`;
  if (link.weight >= 1) return "Shared with 1 other piece";
  const others = Math.round(1 / link.weight);
  return `Shared with ${others.toLocaleString("en-US")} other pieces`;
}

export function truncate(text: string, length: number): string {
  return text.length > length ? `${text.slice(0, length - 1).trimEnd()}…` : text;
}

const byStrength = (a: GraphEdge, b: GraphEdge) =>
  b.weight - a.weight ||
  a.source.localeCompare(b.source) ||
  a.target.localeCompare(b.target) ||
  a.relation.localeCompare(b.relation);

export function graphView(graph: ProductGraph, productId: number): GraphView {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const focusNode = graph.nodes.find((node) => node.product_id === productId) ?? null;
  const edges = graph.edges
    .filter((edge) => nodes.has(edge.source) && nodes.has(edge.target))
    .sort(byStrength);

  // The focus and its strongest neighbors, at most MAX_NODES in all.
  const visible = new Map<string, { node: GraphNode; rank: number }>();
  if (focusNode) {
    visible.set(focusNode.id, { node: focusNode, rank: -1 });
    edges.forEach((edge, rank) => {
      if (visible.size >= MAX_NODES) return;
      if (edge.source !== focusNode.id && edge.target !== focusNode.id) return;
      const neighbor = edge.source === focusNode.id ? edge.target : edge.source;
      const node = nodes.get(neighbor);
      if (node && !visible.has(neighbor)) visible.set(neighbor, { node, rank });
    });
  }

  // Products first (strongest first), then attributes and categories, around the circle
  // from the top, clockwise.
  const neighbors = [...visible.values()]
    .filter(({ node }) => node !== focusNode)
    .sort(
      (a, b) =>
        KIND_ORDER[a.node.kind] - KIND_ORDER[b.node.kind] ||
        (a.node.kind === "product" ? a.rank - b.rank : a.node.id.localeCompare(b.node.id)),
    );
  const point = (node: GraphNode, x: number, y: number, focus: boolean): GraphPoint => ({
    id: node.id,
    kind: node.kind,
    productId: node.product_id ?? null,
    focus,
    label: node.label,
    ...displayLabel(node),
    x,
    y,
  });
  const points = neighbors.map(({ node }, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / neighbors.length;
    const round = (value: number) => Math.round(value * 1e6) / 1e6;
    return point(node, round(Math.cos(angle)), round(Math.sin(angle)), false);
  });
  const focus = focusNode ? point(focusNode, 0, 0, true) : null;

  const links: GraphLink[] = [];
  for (const edge of edges) {
    if (links.length === MAX_LINKS) break;
    const source = visible.get(edge.source)?.node;
    const target = visible.get(edge.target)?.node;
    if (!source || !target) continue;
    links.push({
      source: edge.source,
      target: edge.target,
      relation: edge.relation,
      weight: edge.weight,
      sourceLabel: source.label,
      targetLabel: target.label,
    });
  }
  return { focus, points: focus ? [focus, ...points] : points, links };
}
