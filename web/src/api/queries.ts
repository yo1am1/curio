/** TanStack Query hooks for the API's read endpoints. */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { getJson } from "./client";
import type {
  Book,
  BookHit,
  Cocktail,
  CocktailHit,
  DemoCustomer,
  DomainInfo,
  Health,
  Product,
  ProductHit,
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
