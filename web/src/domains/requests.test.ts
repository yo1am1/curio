import { describe, expect, it } from "vitest";

import { emptySession } from "../state/session";
import {
  becauseBody,
  bookSearchBody,
  browseBody,
  cocktailSearchBody,
  pantryBody,
  textSearchBody,
  uploadProblem,
} from "./requests";

const noTastes = emptySession().cocktailPreferences;
const noReading = emptySession().bookPreferences;

describe("fashion requests", () => {
  it("searches a trimmed description, in a section when one is chosen", () => {
    expect(textSearchBody("  ", null)).toBeNull();
    expect(textSearchBody(" white sneakers ", "Menswear")).toEqual({
      query: "white sneakers",
      top_k: 9,
      section: "Menswear",
    });
  });

  it("checks a photo's type and size before uploading", () => {
    expect(uploadProblem({ type: "image/png", size: 1000 })).toBeNull();
    expect(uploadProblem({ type: "image/gif", size: 1000 })).toMatch(/PNG or JPEG/);
    expect(uploadProblem({ type: "image/jpeg", size: 11 * 1024 * 1024 })).toMatch(/10 MB/);
  });
});

describe("cocktail requests", () => {
  it("waits for a description or a filter", () => {
    const empty = { query: " ", ingredients: [], alcohol: "any" as const, category: null };
    expect(cocktailSearchBody(empty, noTastes)).toBeNull();
    expect(cocktailSearchBody({ ...empty, category: "Shot" }, noTastes)).toMatchObject({
      category: "Shot",
    });
  });

  it("sends every filter, as the Streamlit form does", () => {
    expect(
      cocktailSearchBody(
        {
          query: " minty ",
          ingredients: ["Lime", "Mint"],
          alcohol: "alcoholic",
          category: "Cocktail",
        },
        noTastes,
      ),
    ).toEqual({
      query: "minty",
      ingredients: ["Lime", "Mint"],
      alcohol: "alcoholic",
      category: "Cocktail",
      exclude_ingredients: [],
      top_k: 12,
    });
  });

  it("applies remembered tastes: alcohol-free by default and avoided ingredients", () => {
    const tastes = {
      alcohol_free: true,
      liked_ingredients: [],
      avoided_ingredients: ["Gin", "lime"],
    };
    const body = cocktailSearchBody(
      { query: "sour", ingredients: ["Lime"], alcohol: "any", category: null },
      tastes,
    );
    // An ingredient asked for explicitly is not excluded.
    expect(body).toMatchObject({ alcohol: "non_alcoholic", exclude_ingredients: ["Gin"] });
    // Choosing "Alcoholic" overrides the remembered default.
    expect(
      cocktailSearchBody(
        { query: "sour", ingredients: [], alcohol: "alcoholic", category: null },
        tastes,
      )?.alcohol,
    ).toBe("alcoholic");
  });

  it("asks the pantry with at least one ingredient, within the API's bounds", () => {
    expect(pantryBody({ ingredients: [], alcohol: "any", maxMissing: 2 }, noTastes)).toBeNull();
    expect(
      pantryBody({ ingredients: ["Gin", "Tonic water"], alcohol: "any", maxMissing: 7 }, noTastes),
    ).toEqual({ ingredients: ["Gin", "Tonic water"], alcohol: "any", max_missing: 3, top_k: 9 });
  });
});

describe("book requests", () => {
  const memory = {
    liked_genres: ["Fantasy"],
    avoided_genres: ["Horror"],
    avoided_authors: ["Stephen King"],
  };

  it("searches with genres, mood and length, and the remembered tastes", () => {
    expect(bookSearchBody({ query: "", genres: [], mood: null, maxPages: 300 }, memory)).toBeNull();
    expect(
      bookSearchBody({ query: " cosy ", genres: ["Mystery"], mood: "Cosy", maxPages: 300 }, memory),
    ).toEqual({
      query: "cosy",
      genres: ["Mystery"],
      mood: "Cosy",
      max_pages: 300,
      boost_genres: ["Fantasy"],
      exclude_genres: ["Horror"],
      exclude_authors: ["Stephen King"],
      top_k: 12,
    });
  });

  it("starts Because you liked from the loved books, without boosting", () => {
    expect(becauseBody([], memory)).toBeNull();
    expect(becauseBody([5, 192], memory)).toEqual({
      liked: [5, 192],
      exclude_genres: ["Horror"],
      exclude_authors: ["Stephen King"],
      top_k: 12,
    });
  });

  it("browses a mood, a genre or both", () => {
    expect(browseBody(null, null, noReading)).toBeNull();
    expect(browseBody("Dark", "Thriller", noReading)).toEqual({
      query: "",
      mood: "Dark",
      genres: ["Thriller"],
      boost_genres: [],
      exclude_genres: [],
      exclude_authors: [],
      top_k: 12,
    });
  });
});
