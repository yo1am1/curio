/**
 * The client's domain registry, mirroring the Streamlit client's `DOMAIN_VIEWS`
 * (src/curio/app/ui/shell.py and common.py): each domain's label, copy, grounding badges,
 * approval tools and credit line. Which domains are served comes from `/domains`.
 */
import type { ApprovalTool, Domain, Health } from "../api/types";

export const DOMAIN_ORDER: readonly Domain[] = ["fashion", "cocktails", "books", "evening"];
/** A new visit opens on Plan my evening when the server serves the evening. */
export const DEFAULT_DOMAIN: Domain = "evening";

export type Tone = "green" | "orange" | "red" | "violet" | "blue" | "gray" | "accent";
export type GroundingStatus = "verified" | "repaired" | "fallback";
export type Badge = { label: string; tone: Tone };

export interface DomainCopy {
  title: string;
  intro: string;
  placeholder: string;
  welcome: string;
  starters: readonly string[];
  unavailable: string;
  memoryHint: string;
  shortlistHint: string;
  comparePrompt: string;
  askMemory: string;
  askShortlist: string;
}

/** Approval policy settings shared by every domain, as in the Streamlit session panel. */
export type PolicySetting = "ask_shortlist" | "ask_memory";

export interface DomainView {
  id: Domain;
  label: string;
  unavailable: string;
  setupHint: string;
  copy: DomainCopy;
  badges: Record<GroundingStatus, Badge>;
  approvalTools: Partial<Record<PolicySetting, ApprovalTool>>;
  credit: (health: Health | undefined) => string;
}

export const LLM_HINT =
  "set LLM_MODEL (and LLM_API_KEY / LLM_BASE_URL) in .env, then restart the API.";
export const RECIPE_CREDIT = "Recipe data: TheCocktailDB · Please drink responsibly.";
export const BOOK_SOURCE = "Book data: goodbooks-10k from Goodreads (CC BY-SA 4.0)";
export const BOOK_RATINGS = "Ratings are Goodreads averages, not Curio's.";
export const COVER_CREDIT = "Some covers: Open Library";
export const BOOK_CREDIT = `${BOOK_SOURCE} · ${BOOK_RATINGS}`;
export const EVENING_CREDIT =
  "Outfits: H&M research catalog · Books: goodbooks-10k (Goodreads ratings) · " +
  "Drinks: TheCocktailDB · Please drink responsibly.";
export const FASHION_NOTE =
  "H&M research catalog, 2020. Prices are approximate; current stock is unknown.";
export const QUICK_START = "uv run python -m scripts.prepare_cocktails";

const count = (value: number | undefined) => (value ? value.toLocaleString("en-US") : "");

export const DOMAINS: Record<Domain, DomainView> = {
  fashion: {
    id: "fashion",
    label: "Fashion",
    unavailable: "Fashion is not available",
    setupHint:
      "The H&M fashion data is not prepared on this server (see the technical guide's " +
      "fashion data steps).",
    copy: {
      title: "Find your next favorite.",
      intro:
        "Tell Curio your style, occasion or budget. It searches, compares and asks before " +
        "changing anything you own.",
      placeholder: "Describe a piece or ask a follow-up…",
      welcome:
        "Tell me what you're looking for, including a colour, occasion or budget. " +
        "Try **white sneakers under €40**.",
      starters: [
        "White sneakers under €40",
        "A linen shirt for summer",
        "Warm knitwear",
        "Outfit for an occasion",
      ],
      unavailable:
        "The assistant is currently unavailable. " +
        "You can still discover pieces by description or photo.",
      memoryHint: "Nothing yet. Say “I always shop menswear” and Curio will ask to remember.",
      shortlistHint: "Save pieces from answers, or ask Curio to shortlist them.",
      comparePrompt: "Compare the pieces in my shortlist and tell me which to pick.",
      askMemory: "Ask before remembering preferences",
      askShortlist: "Ask before saving to my shortlist",
    },
    badges: {
      verified: { label: "Prices verified against the catalog", tone: "green" },
      repaired: { label: "Self-corrected to match the catalog", tone: "orange" },
      fallback: { label: "Unverified answer replaced with catalog facts", tone: "red" },
    },
    approvalTools: { ask_shortlist: "SaveToShortlist", ask_memory: "RememberPreferences" },
    credit: (health) => {
      const indexed = (health?.indexed_products ?? 0).toLocaleString("en-US");
      const total = health?.catalog_products ? ` of ${count(health.catalog_products)}` : "";
      return `H&M 2020 research catalog · Photo search: ${indexed}${total} pieces`;
    },
  },
  cocktails: {
    id: "cocktails",
    label: "Cocktails",
    unavailable: "Cocktails are not available",
    setupHint:
      "Cocktail recipes are not prepared on this server yet (run scripts.prepare_cocktails).",
    copy: {
      title: "Mix something new tonight.",
      intro:
        "Tell Curio a flavour, a mood or what's in your cupboard. It finds recipes, " +
        "explains them and asks before changing anything you keep.",
      placeholder: "Describe a drink or ask a follow-up…",
      welcome:
        "Tell me a flavour, an ingredient you have, or whether you'd like it alcohol-free. " +
        "Try **something fresh with lime and mint**.",
      starters: [
        "Something fresh with lime and mint",
        "Alcohol-free drinks",
        "What can I make with gin and tonic?",
      ],
      unavailable:
        "The cocktail assistant is currently unavailable. " +
        "You can still search recipes on the Cocktails page.",
      memoryHint: "Nothing yet. Say “I don't drink alcohol” and Curio will ask to remember.",
      shortlistHint: "Save drinks from answers, or ask Curio to shortlist them.",
      comparePrompt: "Compare the drinks in my shortlist and tell me which to make.",
      askMemory: "Ask before remembering tastes",
      askShortlist: "Ask before saving to my shortlist",
    },
    badges: {
      verified: { label: "Checked against the recipes", tone: "green" },
      repaired: { label: "Self-corrected to match the recipes", tone: "orange" },
      fallback: { label: "Unverified answer replaced with recipe facts", tone: "red" },
    },
    approvalTools: { ask_shortlist: "SaveCocktails", ask_memory: "RememberTastes" },
    credit: (health) => {
      const recipes = health?.cocktail_count ? `${count(health.cocktail_count)} recipes · ` : "";
      return `Recipe data: TheCocktailDB · ${recipes}Please drink responsibly.`;
    },
  },
  books: {
    id: "books",
    label: "Books",
    unavailable: "Books are not available",
    setupHint: "The book catalog is not prepared on this server yet (run scripts.prepare_books).",
    copy: {
      title: "Find your next great read.",
      intro:
        "Tell Curio a book you loved, a genre or a mood. It finds what similar readers " +
        "enjoyed, explains why and asks before changing your reading list.",
      placeholder: "Describe a book or ask a follow-up…",
      welcome:
        "Tell me a book you loved, a genre or a mood. Try **fantasy like The Name of the Wind**.",
      starters: [
        "Fantasy like The Name of the Wind",
        "Short classic novels",
        "A cosy mystery",
        "Pair a cocktail with my reading list",
      ],
      unavailable:
        "The book assistant is currently unavailable. " +
        "You can still search and browse on the Books page.",
      memoryHint:
        "Nothing yet. Say “I love fantasy, but no horror” and Curio will ask to remember.",
      shortlistHint: "Save books from answers, or ask Curio to add them to your reading list.",
      comparePrompt: "Compare the books on my reading list and tell me which to read first.",
      askMemory: "Ask before remembering reading tastes",
      askShortlist: "Ask before adding to my reading list",
    },
    badges: {
      verified: { label: "Titles and ratings checked against the catalog", tone: "green" },
      repaired: { label: "Self-corrected to match the catalog", tone: "orange" },
      fallback: { label: "Unverified answer replaced with catalog facts", tone: "red" },
    },
    approvalTools: { ask_shortlist: "SaveBooks", ask_memory: "RememberReadingTastes" },
    credit: (health) => {
      const books = health?.book_count ? `${count(health.book_count)} books · ` : "";
      return `${BOOK_SOURCE} · ${books}${BOOK_RATINGS} · ${COVER_CREDIT}`;
    },
  },
  evening: {
    id: "evening",
    label: "Evening",
    unavailable: "Evening is not available",
    setupHint:
      "Plan my evening needs the book catalog and the cocktail recipes on this server (run " +
      "scripts.prepare_books and scripts.prepare_cocktails).",
    copy: {
      title: "Plan my evening.",
      intro:
        "Tell Curio the occasion. It plans an outfit, a book and a drink paired with that book " +
        "under one theme, explains why they go together and asks before saving anything.",
      placeholder: "Describe the evening or ask for a change…",
      welcome:
        "Tell me the occasion and I'll plan the outfit, the book and the drink. Try **rainy " +
        "Sunday in** or **date night under €100**.",
      starters: ["Rainy Sunday in", "Date night under €100", "Book club, alcohol-free"],
      unavailable:
        "The evening assistant is currently unavailable. " +
        "You can still plan on the Plan my evening page.",
      memoryHint: "Nothing yet. Tastes you remember in Fashion, Books or Cocktails apply here.",
      shortlistHint: "Saving an evening adds each part to its own list.",
      comparePrompt: "Plan my evening",
      askMemory: "Ask before remembering tastes",
      askShortlist: "Ask before saving an evening",
    },
    badges: {
      verified: { label: "Prices, titles and drinks checked", tone: "green" },
      repaired: { label: "Self-corrected to match the catalogs", tone: "orange" },
      fallback: { label: "Unverified answer replaced with catalog facts", tone: "red" },
    },
    approvalTools: { ask_shortlist: "SaveEvening" },
    credit: () => EVENING_CREDIT,
  },
};

export function isDomain(value: string | undefined): value is Domain {
  return value !== undefined && (DOMAIN_ORDER as readonly string[]).includes(value);
}

/** Where a domain opens: Evening on Plan my evening, the others on their chat (stage 1). */
export function landingPath(domain: Domain): string {
  return domain === "evening" ? "/evening" : askPath(domain);
}

export function askPath(domain: Domain): string {
  return `/ask/${domain}`;
}

/** Friendly names for the agent's steps (the Streamlit client's `STEP_LABELS`). */
export const STEP_LABELS: Record<string, string> = {
  "Understand request": "Plan",
  SearchCatalog: "Search catalog",
  InspectProducts: "Inspect products",
  FindRelated: "Find related pieces",
  PersonalPicks: "Personal picks",
  "Plan outfit": "Plan outfit",
  "Fill slot": "Fill slot",
  BuildOutfit: "Build outfit",
  SwapOutfitItem: "Swap a piece",
  SearchCocktails: "Search recipes",
  InspectCocktails: "Inspect recipes",
  SimilarCocktails: "Find similar drinks",
  WhatCanIMake: "Check your ingredients",
  SearchBooks: "Search books",
  InspectBooks: "Inspect books",
  ReadersAlsoLiked: "Readers also liked",
  BooksByAuthor: "More by the author",
  PairCocktail: "Pair a drink",
  AskShopper: "Question for you",
  SaveToShortlist: "Shortlist proposal",
  RememberPreferences: "Memory proposal",
  SaveCocktails: "Shortlist proposal",
  RememberTastes: "Memory proposal",
  SaveBooks: "Reading list proposal",
  PlanEvening: "Plan evening",
  SwapEveningPart: "Swap a part",
  SaveEvening: "Save evening proposal",
  "Evening theme": "Evening theme",
  "Evening outfit": "Outfit",
  "Evening book": "Book",
  "Evening drink": "Drink",
  RememberReadingTastes: "Memory proposal",
  "Draft answer": "Draft answer",
  "Grounding check": "Grounding check",
  "Self-correction": "Self-correction",
  "Tool budget": "Tool budget",
  "Time budget": "Time budget",
};

export function stepLabel(name: string): string {
  return STEP_LABELS[name] ?? name;
}

/** Client-held list and memory limits (the API's `TurnContext` limits). */
export const MAX_SHORTLIST = 20;
export const MAX_TASTES = 10;
export const PREFERENCE_LIMITS = { sections: 3, colours: 5 } as const;
