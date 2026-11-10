import { describe, expect, it } from "vitest";

import { DOMAINS, pageName } from "./registry";

describe("pageName", () => {
  it("names a page as a screen reader hears it after a navigation", () => {
    expect(pageName("books", "/reading/moods")).toBe("Books: Browse by mood");
    expect(pageName("cocktails", "/ask/cocktails")).toBe("Cocktails: Ask Curio");
    expect(pageName("fashion", "/fashion/quality")).toBe("Fashion: Model quality");
    expect(pageName("evening", "/evening/unknown")).toBe("Evening");
    expect(pageName(null, "/nowhere")).toBe("Curio");
  });
});

describe("the evening credit", () => {
  it("credits H&M only when the fashion catalog is served", () => {
    expect(DOMAINS.evening.credit(undefined, true)).toContain("H&M");
    const without = DOMAINS.evening.credit(undefined, false);
    expect(without).not.toContain("H&M");
    expect(without).toContain("goodbooks-10k");
  });
});
