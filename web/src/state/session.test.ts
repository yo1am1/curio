import { describe, expect, it } from "vitest";

import { book, cocktail, product } from "../test/fixtures";
import { chatPayload, decisionPayload, historyItem, type ChatEntry } from "./chat";
import {
  applyEffects,
  buildContext,
  type EffectItems,
  emptySession,
  forget,
  loadSession,
  memoryChips,
  mergeNewest,
  rememberPreferences,
  restoreSession,
  saveSession,
  SESSION_KEY,
  type Session,
} from "./session";
import { createAppStores } from "./stores";
import { proposal } from "../test/fixtures";

function filled(): Session {
  return {
    ...emptySession(),
    shortlist: [product(11), product(12)],
    cocktailShortlist: [cocktail(7)],
    readingList: [book(5)],
    preferences: { sections: ["Menswear"], colours: [], max_price: 40 },
    cocktailPreferences: {
      alcohol_free: true,
      liked_ingredients: [],
      avoided_ingredients: ["Gin"],
    },
    bookPreferences: { liked_genres: ["Fantasy"], avoided_genres: [], avoided_authors: [] },
    outfit: { occasion: "office", pieces: [] },
    evening: {
      occasion: "date night",
      theme: "romantic",
      alcohol_free: false,
    },
    policy: { ask_shortlist: false, ask_memory: true },
    shopperId: 42,
  };
}

describe("session state", () => {
  it("round-trips through localStorage and survives a reload of the stores", () => {
    const session = filled();
    saveSession(localStorage, session);
    expect(loadSession(localStorage)).toEqual(session);
    const stores = createAppStores(localStorage, sessionStorage);
    expect(stores.session.get()).toEqual(session);
    stores.session.set((s) => ({ ...s, shopperId: 7 }));
    expect(createAppStores(localStorage, sessionStorage).session.get().shopperId).toBe(7);
  });

  it("repairs malformed or missing storage field by field", () => {
    expect(loadSession(localStorage)).toEqual(emptySession());
    localStorage.setItem(SESSION_KEY, "{not json");
    expect(loadSession(localStorage)).toEqual(emptySession());
    const repaired = restoreSession({
      shortlist: [{ nope: 1 }, product(3)],
      policy: { ask_memory: false },
    });
    expect(repaired.shortlist.map((p) => p.product_id)).toEqual([3]);
    expect(repaired.policy).toEqual({ ask_shortlist: true, ask_memory: false });
  });

  it("builds each domain's TurnContext field for field", () => {
    const session = filled();
    expect(buildContext("fashion", session)).toEqual({
      preferences: { sections: ["Menswear"], max_price: 40 },
      shopper_id: 42,
      shortlist: [11, 12],
      outfit: session.outfit,
    });
    expect(buildContext("cocktails", session)).toEqual({
      domain: "cocktails",
      cocktail_preferences: { alcohol_free: true, avoided_ingredients: ["Gin"] },
      cocktail_shortlist: [7],
    });
    expect(buildContext("books", session)).toEqual({
      domain: "books",
      book_preferences: { liked_genres: ["Fantasy"] },
      book_shortlist: [5],
      cocktail_preferences: { alcohol_free: true, avoided_ingredients: ["Gin"] },
    });
    expect(Object.keys(buildContext("evening", session))).toEqual([
      "domain",
      "preferences",
      "book_preferences",
      "cocktail_preferences",
      "shortlist",
      "book_shortlist",
      "cocktail_shortlist",
      "evening",
    ]);
    // An empty session sends nothing but the domain.
    expect(buildContext("fashion", emptySession())).toEqual({});
    expect(buildContext("books", emptySession())).toEqual({ domain: "books" });
  });

  it("sends recent history with card IDs, the policy, and decisions", () => {
    const entries: ChatEntry[] = [
      { id: "1", role: "user", content: "Black trousers" },
      {
        id: "2",
        role: "assistant",
        content: "Here you go",
        reply: {
          text: "Here you go",
          intent: { query: "", wants_products: true },
          status: "complete",
          domain: "fashion",
          products: [1, 2, 3, 4, 5, 6].map((id) => product(id)),
        },
      },
    ];
    expect(historyItem(entries[1] as ChatEntry)).toEqual({
      role: "assistant",
      content: "Here you go",
      product_ids: [1, 2, 3, 4, 5],
    });
    const session = filled();
    const payload = chatPayload("fashion", entries, session);
    expect(payload.history).toHaveLength(2);
    expect(payload.auto_approve).toEqual(["SaveToShortlist"]);
    const action = proposal("SaveToShortlist", { product_ids: [1, 2] });
    const decision = decisionPayload("fashion", entries, session, action, true, {
      product_ids: [1],
    });
    expect(decision.decision).toEqual({
      action,
      approved: true,
      edited_args: { product_ids: [1] },
    });
    expect(decision).not.toHaveProperty("message");
  });

  it("applies approved effects to the lists and memory", () => {
    const items: EffectItems = {
      products: new Map([[1, product(1)]]),
      cocktails: new Map([[7, cocktail(7)]]),
      books: new Map([[5, book(5)]]),
    };
    let { session, notices } = applyEffects(
      emptySession(),
      { applied: [{ kind: "shortlist", product_ids: [1] }] },
      items,
    );
    expect(session.shortlist.map((p) => p.product_id)).toEqual([1]);
    expect(notices[0]?.message).toBe("Saved 1 piece to your shortlist.");
    ({ session, notices } = applyEffects(
      session,
      { applied: [{ kind: "evening", product_ids: [1], book_ids: [5], drink_ids: [7] }] },
      items,
    ));
    expect(session.readingList).toHaveLength(1);
    expect(session.cocktailShortlist).toHaveLength(1);
    expect(notices[0]?.message).toBe("Saved the evening: Book 5, Drink 7.");
    ({ session } = applyEffects(
      session,
      { applied: [{ kind: "cocktail_preferences", cocktail_preferences: { alcohol_free: true } }] },
      items,
    ));
    expect(session.cocktailPreferences.alcohol_free).toBe(true);
  });

  it("keeps memory within the API's limits, newest values winning", () => {
    expect(mergeNewest(["a", "b", "c"], ["b", "d"], 3)).toEqual([["c", "b", "d"], ["a"]]);
    const { session, dropped } = rememberPreferences(emptySession(), {
      sections: ["Menswear", "Ladieswear", "Divided", "Sport"],
    });
    expect(session.preferences.sections).toEqual(["Ladieswear", "Divided", "Sport"]);
    expect(dropped).toEqual(["Menswear"]);
  });

  it("lists memory as removable chips", () => {
    const session = filled();
    const chips = memoryChips("fashion", session);
    expect(chips.map((c) => c.label)).toEqual(["Menswear", "Under €40"]);
    const forgotten = forget(session, chips[1] as (typeof chips)[number]);
    expect(forgotten.preferences.max_price).toBeNull();
    expect(memoryChips("evening", session).map((c) => c.label)).toEqual([
      "Likes Fantasy",
      "Alcohol-free drinks",
      "Pieces up to €40",
    ]);
  });
});
