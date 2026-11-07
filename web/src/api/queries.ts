/** TanStack Query hooks for the API's read endpoints. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { getJson, postForm, postJson } from "./client";
import type {
  BecauseYouLiked,
  Book,
  BookHit,
  BookQuery,
  BookRef,
  Cocktail,
  CocktailHit,
  CocktailQuery,
  DemoCustomer,
  DomainInfo,
  Evaluation,
  Health,
  PantryMatch,
  PantryRequest,
  Product,
  ProductGraph,
  ProductHit,
  QueryPhoto,
  TextSearchRequest,
  UserProfile,
} from "./types";

const MINUTE = 60_000;

export function useDomains() {
  return useQuery({
    queryKey: ["domains"],
    queryFn: ({ signal }) => getJson<DomainInfo[]>("/domains", signal),
    staleTime: MINUTE,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getJson<Health>("/health", signal),
    staleTime: MINUTE,
  });
}

export function useProduct(id: number) {
  return useQuery({
    queryKey: ["product", id],
    queryFn: ({ signal }) => getJson<Product>(`/products/${id}`, signal),
    staleTime: 10 * MINUTE,
  });
}

export function useProductRelations(id: number, relation: "similar" | "complement") {
  return useQuery({
    queryKey: ["product", id, relation],
    queryFn: ({ signal }) =>
      getJson<ProductHit[]>(
        relation === "similar"
          ? `/products/${id}/similar?top_k=4`
          : `/products/${id}/related?kind=complement&top_k=4`,
        signal,
      ),
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

export function useCocktail(id: number) {
  return useQuery({
    queryKey: ["cocktail", id],
    queryFn: ({ signal }) => getJson<Cocktail>(`/cocktails/${id}`, signal),
    staleTime: 10 * MINUTE,
  });
}

export function useSimilarCocktails(id: number) {
  return useQuery({
    queryKey: ["cocktail", id, "similar"],
    queryFn: ({ signal }) =>
      getJson<CocktailHit[]>(`/cocktails/${id}/similar?top_k=5&alcohol=any`, signal),
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

export function useBook(id: number) {
  return useQuery({
    queryKey: ["book", id],
    queryFn: ({ signal }) => getJson<Book>(`/books/${id}`, signal),
    staleTime: 10 * MINUTE,
  });
}

export function useSimilarBooks(id: number) {
  return useQuery({
    queryKey: ["book", id, "similar"],
    queryFn: ({ signal }) =>
      getJson<BookHit[]>(`/books/${id}/similar?top_k=5&other_authors_only=true`, signal),
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

export function useBooksByAuthor(id: number) {
  return useQuery({
    queryKey: ["book", id, "by-author"],
    queryFn: ({ signal }) => getJson<BookHit[]>(`/books/${id}/by-author?top_k=5`, signal),
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

export function useDemoCustomers(enabled: boolean) {
  return useQuery({
    queryKey: ["demo-customers"],
    queryFn: ({ signal }) => getJson<DemoCustomer[]>("/demo/customers", signal),
    staleTime: 10 * MINUTE,
    enabled,
  });
}

/** The served domains by id (from `/domains`). */
export function useServed(): Map<DomainInfo["id"], DomainInfo> {
  const { data } = useDomains();
  return useMemo(() => new Map((data ?? []).map((info) => [info.id, info])), [data]);
}

// --- Browse pages -------------------------------------------------------------------------

/** A list endpoint that rarely changes (sections, genres, ingredients…). */
function useList<T>(key: string, path: string, enabled = true) {
  return useQuery({
    queryKey: [key],
    queryFn: ({ signal }) => getJson<T>(path, signal),
    staleTime: 30 * MINUTE,
    enabled,
  });
}

/** A search the page runs from its filters: `body` null means "nothing to search yet". The
 * previous results stay on screen (dimmed by the page) while new ones load. */
function useSearch<T>(key: string, path: string, body: object | null) {
  return useQuery({
    queryKey: [key, body],
    queryFn: ({ signal }) => postJson<T>(path, body, signal),
    enabled: body !== null,
    staleTime: 5 * MINUTE,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export const useSections = () => useList<string[]>("sections", "/sections");
export const useSearchExamples = () => useList<QueryPhoto[]>("search-examples", "/search/examples");
export const useUsers = (enabled: boolean) => useList<number[]>("users", "/users", enabled);
export const useCocktailIngredients = () =>
  useList<string[]>("cocktail-ingredients", "/cocktails/ingredients");
export const useCocktailCategories = () =>
  useList<string[]>("cocktail-categories", "/cocktails/categories");
export const useBookGenres = () => useList<string[]>("book-genres", "/books/genres");
export const useBookMoods = () => useList<string[]>("book-moods", "/books/moods");
export const useBookIndex = () => useList<BookRef[]>("book-index", "/books/index");

export function useUserProfile(id: number | null) {
  return useQuery({
    queryKey: ["user", id],
    queryFn: ({ signal }) => getJson<UserProfile>(`/users/${id}`, signal),
    enabled: id !== null,
    staleTime: 10 * MINUTE,
  });
}

export type Strategy = "learned" | "repeat" | "collaborative" | "content" | "graph";

export function useRecommendations(id: number | null, strategy: Strategy, topK = 12) {
  return useQuery({
    queryKey: ["recommendations", id, strategy, topK],
    queryFn: ({ signal }) =>
      getJson<ProductHit[]>(
        `/users/${id}/recommendations?strategy=${strategy}&top_k=${topK}`,
        signal,
      ),
    enabled: id !== null,
    staleTime: 10 * MINUTE,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useProductGraph(id: number | null) {
  return useQuery({
    queryKey: ["product", id, "graph"],
    queryFn: ({ signal }) => getJson<ProductGraph>(`/products/${id}/graph?top_k=12`, signal),
    enabled: id !== null,
    staleTime: 10 * MINUTE,
    retry: false,
  });
}

export type Connection = "complement" | "alternative" | "similar";

export function useConnections(id: number | null, kind: Connection) {
  return useQuery({
    queryKey: ["product", id, "connections", kind],
    queryFn: ({ signal }) =>
      getJson<ProductHit[]>(
        kind === "similar"
          ? `/products/${id}/similar?top_k=9`
          : `/products/${id}/related?kind=${kind}&top_k=9`,
        signal,
      ),
    enabled: id !== null,
    staleTime: 10 * MINUTE,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useEvaluation() {
  return useQuery({
    queryKey: ["evaluation"],
    queryFn: ({ signal }) => getJson<Evaluation>("/evaluation", signal),
    staleTime: 30 * MINUTE,
    retry: false,
  });
}

export const useTextSearch = (body: TextSearchRequest | null) =>
  useSearch<ProductHit[]>("text-search", "/search/text", body);
export const useCocktailSearch = (body: CocktailQuery | null) =>
  useSearch<CocktailHit[]>("cocktail-search", "/cocktails/search", body);
export const usePantry = (body: PantryRequest | null) =>
  useSearch<PantryMatch[]>("pantry", "/cocktails/pantry", body);
export const useBookSearch = (body: BookQuery | null) =>
  useSearch<BookHit[]>("book-search", "/books/search", body);
export const useBecause = (body: BecauseYouLiked | null) =>
  useSearch<BookHit[]>("because", "/books/because", body);

/** A photo to search with: an uploaded file or an example photo, with a stable cache key. */
export interface QueryImage {
  key: string;
  name: string;
  load: () => Promise<Blob>;
}

/** Photo search: the image goes up as multipart form data (`file`). */
export function useImageSearch(image: QueryImage | null, topK = 9) {
  return useQuery({
    queryKey: ["image-search", image?.key, topK],
    queryFn: async ({ signal }) => {
      if (!image) return [];
      const form = new FormData();
      form.append("file", await image.load(), image.name);
      return postForm<ProductHit[]>(`/search/image?top_k=${topK}`, form, signal);
    },
    enabled: image !== null,
    staleTime: 10 * MINUTE,
    retry: false,
  });
}
