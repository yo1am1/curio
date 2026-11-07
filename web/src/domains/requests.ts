/**
 * The browse pages' filters as API request bodies.
 * Each returns null while there is nothing to search yet. Remembered tastes apply: reading
 * tastes boost liked genres and filter avoided genres and authors; remembered
 * cocktail tastes filter avoided ingredients and default the alcohol filter to alcohol-free.
 */
import type {
  AlcoholFilter,
  BecauseYouLiked,
  BookQuery,
  CocktailQuery,
  PantryRequest,
  TextSearchRequest,
} from "../api/types";
import type { CocktailMemory, ReadingMemory } from "../state/session";

export const TOP_K = { pieces: 9, drinks: 12, pantry: 9, books: 12 } as const;
export const LIMITS = { ingredients: 10, pantry: 15, genres: 3, loved: 10 } as const;
export const MAX_UPLOAD_MB = 10;
const PHOTO_TYPES = ["image/png", "image/jpeg"];

export function textSearchBody(query: string, section: string | null): TextSearchRequest | null {
  const text = query.trim();
  if (!text) return null;
  return { query: text.slice(0, 300), top_k: TOP_K.pieces, section };
}

/** Why a file cannot be searched (the API's limits, checked before uploading), or null. */
export function uploadProblem(file: Pick<File, "type" | "size">): string | null {
  if (!PHOTO_TYPES.includes(file.type)) return "Choose a PNG or JPEG photo.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    return `That photo is larger than ${String(MAX_UPLOAD_MB)} MB. Choose a smaller one.`;
  }
  return null;
}

/** "Any" becomes alcohol-free when the shopper asked Curio to remember alcohol-free. */
export function alcoholFor(alcohol: AlcoholFilter, tastes: CocktailMemory): AlcoholFilter {
  return alcohol === "any" && tastes.alcohol_free ? "non_alcoholic" : alcohol;
}

export interface CocktailFilters {
  query: string;
  ingredients: string[];
  alcohol: AlcoholFilter;
  category: string | null;
}

export function cocktailSearchBody(
  filters: CocktailFilters,
  tastes: CocktailMemory,
): CocktailQuery | null {
  const query = filters.query.trim();
  const { ingredients, alcohol, category } = filters;
  if (!query && !ingredients.length && alcohol === "any" && !category) return null;
  const avoided = tastes.avoided_ingredients.filter(
    (name) => !ingredients.some((wanted) => wanted.toLowerCase() === name.toLowerCase()),
  );
  return {
    query: query.slice(0, 200),
    ingredients: ingredients.slice(0, LIMITS.ingredients),
    alcohol: alcoholFor(alcohol, tastes),
    category,
    exclude_ingredients: avoided.slice(0, LIMITS.ingredients),
    top_k: TOP_K.drinks,
  };
}

export interface PantryFilters {
  ingredients: string[];
  alcohol: AlcoholFilter;
  maxMissing: number;
}

export function pantryBody(filters: PantryFilters, tastes: CocktailMemory): PantryRequest | null {
  if (!filters.ingredients.length) return null;
  return {
    ingredients: filters.ingredients.slice(0, LIMITS.pantry),
    alcohol: alcoholFor(filters.alcohol, tastes),
    max_missing: Math.max(0, Math.min(3, Math.round(filters.maxMissing))),
    top_k: TOP_K.pantry,
  };
}

/** Remembered avoidances as search filters, so the pages respect them like the agent. */
function avoided(memory: ReadingMemory) {
  return { exclude_genres: memory.avoided_genres, exclude_authors: memory.avoided_authors };
}

export interface BookFilters {
  query: string;
  genres: string[];
  mood: string | null;
  maxPages: number | null;
}

export function bookSearchBody(filters: BookFilters, memory: ReadingMemory): BookQuery | null {
  const query = filters.query.trim();
  if (!query && !filters.genres.length && !filters.mood) return null;
  return {
    query: query.slice(0, 200),
    genres: filters.genres.slice(0, LIMITS.genres),
    mood: filters.mood,
    max_pages: filters.maxPages,
    boost_genres: memory.liked_genres,
    top_k: TOP_K.books,
    ...avoided(memory),
  };
}

export function becauseBody(liked: number[], memory: ReadingMemory): BecauseYouLiked | null {
  if (!liked.length) return null;
  return { liked: liked.slice(0, LIMITS.loved), top_k: TOP_K.books, ...avoided(memory) };
}

export function browseBody(
  mood: string | null,
  genre: string | null,
  memory: ReadingMemory,
): BookQuery | null {
  if (!mood && !genre) return null;
  return {
    query: "",
    mood,
    genres: genre ? [genre] : [],
    boost_genres: memory.liked_genres,
    top_k: TOP_K.books,
    ...avoided(memory),
  };
}
