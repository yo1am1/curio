import { describe, expect, it } from "vitest";

import type { ProductGraph } from "../../api/types";
import { MAX_NODES, displayLabel, graphView, truncate, weightText } from "./graph";

function star(products: number, attributes: number): ProductGraph {
  const nodes: ProductGraph["nodes"] = [
    { id: "product:1", label: "Mona", kind: "product", product_id: 1 },
  ];
  const edges: ProductGraph["edges"] = [];
  for (let i = 0; i < attributes; i++) {
    const id = `attribute:["color", "C${String(i)}"]`;
    nodes.push({ id, label: `color: C${String(i)}`, kind: "attribute", product_id: null });
    edges.push({ source: "product:1", target: id, relation: "has_attribute", weight: 0.01 });
  }
  for (let i = 0; i < products; i++) {
    const id = `product:${String(100 + i)}`;
    nodes.push({ id, label: `Piece ${String(i)}`, kind: "product", product_id: 100 + i });
    edges.push({ source: "product:1", target: id, relation: "co_purchased", weight: 10 + i });
  }
  return { nodes, edges };
}

describe("graphView", () => {
  it("tells apart products that share a name by their product number", () => {
    const graph = star(2, 0);
    for (const node of graph.nodes) node.label = "Mona";
    const view = graphView(graph, 1);
    expect(view.points.map((p) => [p.title, p.caption])).toEqual([
      ["Mona", "no. 1"],
      ["Mona", "no. 101"],
      ["Mona", "no. 100"],
    ]);
    expect(view.links[0]).toMatchObject({
      sourceLabel: "Mona (no. 1)",
      targetLabel: "Mona (no. 101)",
    });
    // Unique names stay as they are.
    expect(graphView(star(2, 0), 1).points.map((p) => p.caption)).toEqual([null, null, null]);
  });

  it("puts the focus in the middle and its neighbors on a circle, products first", () => {
    const view = graphView(star(3, 2), 1);
    expect(view.focus).toMatchObject({ id: "product:1", x: 0, y: 0, focus: true });
    const neighbors = view.points.filter((p) => !p.focus);
    // Strongest co-purchase first, from the top of the circle.
    expect(neighbors.map((p) => p.id)).toEqual([
      "product:102",
      "product:101",
      "product:100",
      'attribute:["color", "C0"]',
      'attribute:["color", "C1"]',
    ]);
    expect(neighbors[0]).toMatchObject({ x: 0, y: -1 });
    for (const point of neighbors) expect(Math.hypot(point.x, point.y)).toBeCloseTo(1);
    expect(neighbors[0]?.productId).toBe(102);
    expect(neighbors[3]).toMatchObject({ title: "C0", caption: "color", productId: null });
  });

  it("keeps at most 12 nodes and only the edges between them", () => {
    const view = graphView(star(20, 4), 1);
    expect(view.points).toHaveLength(MAX_NODES);
    const ids = new Set(view.points.map((p) => p.id));
    expect(view.links).toHaveLength(MAX_NODES - 1);
    for (const link of view.links) {
      expect(ids.has(link.source) && ids.has(link.target)).toBe(true);
      expect(link.sourceLabel).toBe("Mona");
    }
    // The strongest relationships win the places.
    expect(view.links[0]).toMatchObject({ relation: "co_purchased", weight: 29 });
  });

  it("ignores edges to unknown nodes and has nothing to draw without the focus", () => {
    const graph = star(1, 0);
    graph.edges.push({
      source: "product:1",
      target: "product:999",
      relation: "co_purchased",
      weight: 99,
    });
    expect(graphView(graph, 1).links).toHaveLength(1);
    expect(graphView(graph, 42)).toEqual({ focus: null, points: [], links: [] });
  });
});

describe("graph labels", () => {
  it("shows a category's last level and an attribute's value", () => {
    expect(
      displayLabel({ kind: "taxonomy", label: "Apparel & Accessories > Clothing > Clothing Tops" }),
    ).toEqual({ title: "Clothing Tops", caption: "category" });
    expect(displayLabel({ kind: "attribute", label: "target-gender: Female" })).toEqual({
      title: "Female",
      caption: "target gender",
    });
    expect(displayLabel({ kind: "product", label: "Mona" })).toEqual({
      title: "Mona",
      caption: null,
    });
  });

  it("says what a weight means", () => {
    expect(weightText({ relation: "co_purchased", weight: 12.345 })).toBe("Lift 12.3");
    expect(weightText({ relation: "has_attribute", weight: 0.25 })).toBe(
      "Shared with 4 other pieces",
    );
    expect(weightText({ relation: "in_category", weight: 1 })).toBe("Shared with 1 other piece");
    expect(truncate("Supreme Mock Neck LS TVP", 14)).toBe("Supreme Mock…");
  });
});
