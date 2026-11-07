/**
 * The client-held session: the shortlist, reading list, saved drinks, remembered preferences
 * and tastes, the current outfit and evening, the approval policy and the linked shopper.
 * The server is stateless: every chat request carries this state back (`buildContext`), as the
 * Streamlit client's TurnContext does. It is kept in localStorage.
 */
import type {
  AssistantReply,
  ApprovalTool,
  Book,
  BookPreferences,
  ChatRequest,
  Cocktail,
  CocktailPreferences,
  Domain,
  EveningState,
  EveningTastes,
  OutfitState,
  Product,
} from "../api/types";
import {
  DOMAINS,
  MAX_SHORTLIST,
  MAX_TASTES,
  PREFERENCE_LIMITS,
  type PolicySetting,
} from "../domains/registry";
import { productName } from "../domains/format";
import { readStorage, writeStorage } from "./store";

export const SESSION_KEY = "curio.session.v1";

export interface FashionMemory {
  sections: string[];
  colours: string[];
  max_price: number | null;
}

export interface CocktailMemory {
  alcohol_free: boolean;
  liked_ingredients: string[];
  avoided_ingredients: string[];
}

export interface ReadingMemory {
  liked_genres: string[];
  avoided_genres: string[];
  avoided_authors: string[];
}

export interface Session {
  shortlist: Product[];
  cocktailShortlist: Cocktail[];
  readingList: Book[];
  preferences: FashionMemory;
  cocktailPreferences: CocktailMemory;
  bookPreferences: ReadingMemory;
  outfit: OutfitState | null;
  evening: EveningState | null;
  /** true: ask before the agent saves / remembers (the default). */
  policy: Record<PolicySetting, boolean>;
  shopperId: number | null;
}

export function emptySession(): Session {
  return {
    shortlist: [],
    cocktailShortlist: [],
    readingList: [],
    preferences: { sections: [], colours: [], max_price: null },
    cocktailPreferences: { alcohol_free: false, liked_ingredients: [], avoided_ingredients: [] },
    bookPreferences: { liked_genres: [], avoided_genres: [], avoided_authors: [] },
    outfit: null,
    evening: null,
    policy: { ask_shortlist: true, ask_memory: true },
    shopperId: null,
  };
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const list = <T>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
const strings = (value: unknown): string[] =>
  list<unknown>(value).filter((v): v is string => typeof v === "string");

/** A stored session, repaired field by field: anything missing or malformed gets its default. */
export function restoreSession(raw: unknown): Session {
  const base = emptySession();
  if (!isObject(raw)) return base;
  const prefs = isObject(raw.preferences) ? raw.preferences : {};
  const tastes = isObject(raw.cocktailPreferences) ? raw.cocktailPreferences : {};
  const reading = isObject(raw.bookPreferences) ? raw.bookPreferences : {};
  const policy = isObject(raw.policy) ? raw.policy : {};
  return {
    shortlist: list<Product>(raw.shortlist).filter(
      (p) => isObject(p) && typeof p.product_id === "number",
    ),
    cocktailShortlist: list<Cocktail>(raw.cocktailShortlist).filter(
      (c) => isObject(c) && typeof c.drink_id === "number",
    ),
    readingList: list<Book>(raw.readingList).filter(
      (b) => isObject(b) && typeof b.book_id === "number",
    ),
    preferences: {
      sections: strings(prefs.sections),
      colours: strings(prefs.colours),
      max_price: typeof prefs.max_price === "number" ? prefs.max_price : null,
    },
    cocktailPreferences: {
      alcohol_free: tastes.alcohol_free === true,
      liked_ingredients: strings(tastes.liked_ingredients),
      avoided_ingredients: strings(tastes.avoided_ingredients),
    },
    bookPreferences: {
      liked_genres: strings(reading.liked_genres),
      avoided_genres: strings(reading.avoided_genres),
      avoided_authors: strings(reading.avoided_authors),
    },
    outfit: isObject(raw.outfit) ? (raw.outfit as unknown as OutfitState) : null,
    evening: isObject(raw.evening) ? (raw.evening as unknown as EveningState) : null,
    policy: {
      ask_shortlist: policy.ask_shortlist !== false,
      ask_memory: policy.ask_memory !== false,
    },
    shopperId: typeof raw.shopperId === "number" ? raw.shopperId : null,
  };
}

export function loadSession(storage: Storage | undefined): Session {
  return restoreSession(readStorage(storage, SESSION_KEY));
}

export function saveSession(storage: Storage | undefined, session: Session): void {
  writeStorage(storage, SESSION_KEY, session);
}

// --- Lists ------------------------------------------------------------------------------

export type AddResult = { session: Session; added: boolean; full: boolean };

function addTo<T>(items: T[], item: T, same: (a: T) => boolean): [T[], boolean, boolean] {
  if (items.some(same)) return [items, false, false];
  if (items.length >= MAX_SHORTLIST) return [items, false, true];
  return [[...items, item], true, false];
}

export function addProduct(session: Session, product: Product): AddResult {
  const [shortlist, added, full] = addTo(
    session.shortlist,
    product,
    (p) => p.product_id === product.product_id,
  );
  return { session: added ? { ...session, shortlist } : session, added, full };
}

export function addCocktail(session: Session, cocktail: Cocktail): AddResult {
  const [items, added, full] = addTo(
    session.cocktailShortlist,
    cocktail,
    (c) => c.drink_id === cocktail.drink_id,
  );
  return { session: added ? { ...session, cocktailShortlist: items } : session, added, full };
}

export function addBook(session: Session, book: Book): AddResult {
  const [items, added, full] = addTo(session.readingList, book, (b) => b.book_id === book.book_id);
  return { session: added ? { ...session, readingList: items } : session, added, full };
}

export function removeProduct(session: Session, id: number): Session {
  return { ...session, shortlist: session.shortlist.filter((p) => p.product_id !== id) };
}

export function removeCocktail(session: Session, id: number): Session {
  return {
    ...session,
    cocktailShortlist: session.cocktailShortlist.filter((c) => c.drink_id !== id),
  };
}

export function removeBook(session: Session, id: number): Session {
  return { ...session, readingList: session.readingList.filter((b) => b.book_id !== id) };
}

// --- Memory -----------------------------------------------------------------------------

/** Merge approved values into memory within the API's limit; the newest values win.
 * Returns [kept, dropped]. A value approved again moves to the newest end. */
export function mergeNewest<T>(old: T[], added: T[], limit: number): [T[], T[]] {
  const fresh = [...new Set(added)];
  const merged = [...old.filter((value) => !fresh.includes(value)), ...fresh];
  const cut = Math.max(merged.length - limit, 0);
  return [merged.slice(cut), merged.slice(0, cut)];
}

export function rememberPreferences(
  session: Session,
  prefs: { sections?: string[]; colours?: string[]; max_price?: number | null },
): { session: Session; dropped: string[] } {
  const [sections, lostSections] = mergeNewest(
    session.preferences.sections,
    prefs.sections ?? [],
    PREFERENCE_LIMITS.sections,
  );
  const [colours, lostColours] = mergeNewest(
    session.preferences.colours,
    prefs.colours ?? [],
    PREFERENCE_LIMITS.colours,
  );
  const max_price = prefs.max_price ?? session.preferences.max_price;
  return {
    session: { ...session, preferences: { sections, colours, max_price } },
    dropped: [...lostSections, ...lostColours],
  };
}

export function rememberTastes(
  session: Session,
  tastes: Partial<CocktailPreferences>,
): { session: Session; dropped: string[] } {
  const memory = session.cocktailPreferences;
  const [liked, lostLiked] = mergeNewest(
    memory.liked_ingredients,
    tastes.liked_ingredients ?? [],
    MAX_TASTES,
  );
  const [avoided, lostAvoided] = mergeNewest(
    memory.avoided_ingredients,
    tastes.avoided_ingredients ?? [],
    MAX_TASTES,
  );
  return {
    session: {
      ...session,
      cocktailPreferences: {
        alcohol_free: memory.alcohol_free || tastes.alcohol_free === true,
        liked_ingredients: liked,
        avoided_ingredients: avoided,
      },
    },
    dropped: [...lostLiked, ...lostAvoided],
  };
}

export function rememberReadingTastes(
  session: Session,
  tastes: Partial<BookPreferences>,
): { session: Session; dropped: string[] } {
  const memory = session.bookPreferences;
  const dropped: string[] = [];
  const next = { ...memory };
  for (const field of ["liked_genres", "avoided_genres", "avoided_authors"] as const) {
    const [kept, lost] = mergeNewest(memory[field], tastes[field] ?? [], MAX_TASTES);
    next[field] = kept;
    dropped.push(...lost);
  }
  return { session: { ...session, bookPreferences: next }, dropped };
}

/** A remembered value shown as a removable chip in the session panel. */
export interface MemoryChip {
  memory: "preferences" | "cocktailPreferences" | "bookPreferences";
  field: string;
  value: string | number | boolean;
  label: string;
}

export function fashionChips(memory: FashionMemory): MemoryChip[] {
  const chips: MemoryChip[] = [
    ...memory.sections.map((v) => ({
      memory: "preferences" as const,
      field: "sections",
      value: v,
      label: v,
    })),
    ...memory.colours.map((v) => ({
      memory: "preferences" as const,
      field: "colours",
      value: v,
      label: v,
    })),
  ];
  if (memory.max_price != null) {
    chips.push({
      memory: "preferences",
      field: "max_price",
      value: memory.max_price,
      label: `Under €${memory.max_price}`,
    });
  }
  return chips;
}

export function cocktailChips(memory: CocktailMemory): MemoryChip[] {
  const chips: MemoryChip[] = [];
  const at = "cocktailPreferences" as const;
  if (memory.alcohol_free) {
    chips.push({ memory: at, field: "alcohol_free", value: true, label: "Alcohol-free" });
  }
  chips.push(
    ...memory.liked_ingredients.map((v) => ({
      memory: at,
      field: "liked_ingredients",
      value: v,
      label: `Likes ${v}`,
    })),
    ...memory.avoided_ingredients.map((v) => ({
      memory: at,
      field: "avoided_ingredients",
      value: v,
      label: `Avoids ${v}`,
    })),
  );
  return chips;
}

export function readingChips(memory: ReadingMemory): MemoryChip[] {
  const at = "bookPreferences" as const;
  return [
    ...memory.liked_genres.map((v) => ({
      memory: at,
      field: "liked_genres",
      value: v,
      label: `Likes ${v}`,
    })),
    ...memory.avoided_genres.map((v) => ({
      memory: at,
      field: "avoided_genres",
      value: v,
      label: `No ${v}`,
    })),
    ...memory.avoided_authors.map((v) => ({
      memory: at,
      field: "avoided_authors",
      value: v,
      label: `No ${v}`,
    })),
  ];
}

/** The chips a domain's session panel shows; the evening shows every domain's. */
export function memoryChips(domain: Domain, session: Session): MemoryChip[] {
  switch (domain) {
    case "fashion":
      return fashionChips(session.preferences);
    case "cocktails":
      return cocktailChips(session.cocktailPreferences);
    case "books":
      return readingChips(session.bookPreferences);
    case "evening": {
      const chips = readingChips(session.bookPreferences);
      if (session.cocktailPreferences.alcohol_free) {
        chips.push({
          memory: "cocktailPreferences",
          field: "alcohol_free",
          value: true,
          label: "Alcohol-free drinks",
        });
      }
      if (session.preferences.max_price != null) {
        chips.push({
          memory: "preferences",
          field: "max_price",
          value: session.preferences.max_price,
          label: `Pieces up to €${session.preferences.max_price}`,
        });
      }
      return chips;
    }
  }
}

export function forget(session: Session, chip: MemoryChip): Session {
  if (chip.memory === "preferences") {
    const memory = session.preferences;
    return {
      ...session,
      preferences:
        chip.field === "max_price"
          ? { ...memory, max_price: null }
          : {
              ...memory,
              [chip.field]: memory[chip.field as "sections" | "colours"].filter(
                (v) => v !== chip.value,
              ),
            },
    };
  }
  if (chip.memory === "cocktailPreferences") {
    const memory = session.cocktailPreferences;
    return {
      ...session,
      cocktailPreferences:
        chip.field === "alcohol_free"
          ? { ...memory, alcohol_free: false }
          : {
              ...memory,
              [chip.field]: memory[
                chip.field as "liked_ingredients" | "avoided_ingredients"
              ].filter((v) => v !== chip.value),
            },
    };
  }
  const memory = session.bookPreferences;
  const field = chip.field as keyof ReadingMemory;
  return {
    ...session,
    bookPreferences: { ...memory, [field]: memory[field].filter((v) => v !== chip.value) },
  };
}

// --- Requests -----------------------------------------------------------------------------

/** Only the remembered fields that hold something, as the Streamlit client sends them. */
function present<T extends object>(memory: T): Partial<T> | null {
  const kept = Object.fromEntries(
    Object.entries(memory).filter(([, value]) =>
      Array.isArray(value) ? value.length > 0 : Boolean(value),
    ),
  ) as Partial<T>;
  return Object.keys(kept).length ? kept : null;
}

export type TurnContext = Omit<Partial<ChatRequest>, "message" | "history" | "decision">;

/** This domain's fields of a chat request, field for field the Streamlit client's
 * `*_context()`. Unset state is left out, and fashion requests leave `domain` out, so they
 * look exactly as they did before the other domains existed. */
export function buildContext(domain: Domain, session: Session): TurnContext {
  const prefs = present(session.preferences) as TurnContext["preferences"] | null;
  const tastes = present(session.cocktailPreferences) as TurnContext["cocktail_preferences"] | null;
  const reading = present(session.bookPreferences) as TurnContext["book_preferences"] | null;
  const shortlist = session.shortlist.map((p) => p.product_id);
  const drinks = session.cocktailShortlist.map((c) => c.drink_id);
  const books = session.readingList.map((b) => b.book_id);
  const context: TurnContext = {};
  switch (domain) {
    case "fashion":
      if (prefs) context.preferences = prefs;
      if (session.shopperId != null) context.shopper_id = session.shopperId;
      if (shortlist.length) context.shortlist = shortlist;
      if (session.outfit) context.outfit = session.outfit;
      break;
    case "cocktails":
      context.domain = domain;
      if (tastes) context.cocktail_preferences = tastes;
      if (drinks.length) context.cocktail_shortlist = drinks;
      break;
    case "books":
      context.domain = domain;
      if (reading) context.book_preferences = reading;
      if (books.length) context.book_shortlist = books;
      // Drink pairings honour the remembered cocktail tastes.
      if (tastes) context.cocktail_preferences = tastes;
      break;
    case "evening":
      // An evening reads every domain's tastes and lists, plus the current plan.
      context.domain = domain;
      if (prefs) context.preferences = prefs;
      if (reading) context.book_preferences = reading;
      if (tastes) context.cocktail_preferences = tastes;
      if (shortlist.length) context.shortlist = shortlist;
      if (books.length) context.book_shortlist = books;
      if (drinks.length) context.cocktail_shortlist = drinks;
      if (session.evening) context.evening = session.evening;
      break;
  }
  return context;
}

/** Approval tools that run without asking, from the policy toggles. */
export function autoApprove(domain: Domain, session: Session): ApprovalTool[] {
  return Object.entries(DOMAINS[domain].approvalTools)
    .filter(([setting]) => !session.policy[setting as PolicySetting])
    .map(([, tool]) => tool);
}

/** Remembered tastes from the other domains that shape an evening on the planner page. */
export function eveningTastes(session: Session): Partial<EveningTastes> {
  const tastes: Partial<EveningTastes> = {};
  const reading = present(session.bookPreferences);
  const drinks = present(session.cocktailPreferences);
  if (reading) tastes.book_preferences = reading;
  if (drinks) tastes.cocktail_preferences = drinks as EveningTastes["cocktail_preferences"];
  if (session.preferences.max_price != null) tastes.max_item_price = session.preferences.max_price;
  return tastes;
}

// --- Approved effects ---------------------------------------------------------------------

export interface EffectItems {
  products: Map<number, Product>;
  cocktails: Map<number, Cocktail>;
  books: Map<number, Book>;
}

export interface Fetchers {
  product: (id: number) => Promise<Product | null>;
  cocktail: (id: number) => Promise<Cocktail | null>;
  book: (id: number) => Promise<Book | null>;
}

type Reply = Pick<AssistantReply, "applied" | "products" | "cocktails" | "books">;

/** The cards an effect names: from the reply, else fetched by ID (as effects.py does). */
export async function effectItems(reply: Reply, fetchers: Fetchers): Promise<EffectItems> {
  const items: EffectItems = {
    products: new Map((reply.products ?? []).map((p) => [p.product_id, p])),
    cocktails: new Map((reply.cocktails ?? []).map((c) => [c.drink_id, c])),
    books: new Map((reply.books ?? []).map((b) => [b.book_id, b])),
  };
  const wanted = {
    products: new Set<number>(),
    cocktails: new Set<number>(),
    books: new Set<number>(),
  };
  for (const effect of reply.applied ?? []) {
    for (const id of ("product_ids" in effect ? effect.product_ids : undefined) ?? []) {
      if (!items.products.has(id)) wanted.products.add(id);
    }
    for (const id of ("drink_ids" in effect ? effect.drink_ids : undefined) ?? []) {
      if (!items.cocktails.has(id)) wanted.cocktails.add(id);
    }
    for (const id of ("book_ids" in effect ? effect.book_ids : undefined) ?? []) {
      if (!items.books.has(id)) wanted.books.add(id);
    }
  }
  const fetchInto = async <T>(
    ids: Set<number>,
    fetch: (id: number) => Promise<T | null>,
    into: Map<number, T>,
  ) => {
    await Promise.all(
      [...ids].map(async (id) => {
        const item = await fetch(id).catch(() => null);
        if (item) into.set(id, item);
      }),
    );
  };
  await Promise.all([
    fetchInto(wanted.products, fetchers.product, items.products),
    fetchInto(wanted.cocktails, fetchers.cocktail, items.cocktails),
    fetchInto(wanted.books, fetchers.book, items.books),
  ]);
  return items;
}

export interface Notice {
  message: string;
  tone: "success" | "info";
}

const saved = (n: number, noun: string) =>
  `Saved ${n} ${n === 1 ? noun : `${noun}s`} to your shortlist.`;

function memoryNotices(dropped: string[]): Notice[] {
  const notices: Notice[] = [
    { message: "Curio will remember that for this session.", tone: "success" },
  ];
  if (dropped.length) {
    notices.push({
      message: `Memory is full, so Curio forgot older values: ${dropped.join(", ")}.`,
      tone: "info",
    });
  }
  return notices;
}

/** Apply a reply's approved effects to the session (effects.py): what the agent saved joins
 * the lists, what it remembered joins memory. Returns the notices to show. */
export function applyEffects(
  session: Session,
  reply: Pick<AssistantReply, "applied">,
  items: EffectItems,
): { session: Session; notices: Notice[] } {
  let next = session;
  const notices: Notice[] = [];
  for (const effect of reply.applied ?? []) {
    if (effect.kind === "shortlist") {
      const ids = effect.product_ids ?? [];
      let added = 0;
      for (const id of ids) {
        const product = items.products.get(id);
        if (!product) continue;
        const result = addProduct(next, product);
        next = result.session;
        added += Number(result.added);
      }
      if (added) notices.push({ message: saved(added, "piece"), tone: "success" });
      if (added < ids.length && next.shortlist.length >= MAX_SHORTLIST) {
        notices.push({
          message: `Your shortlist holds up to ${MAX_SHORTLIST} pieces.`,
          tone: "info",
        });
      }
    } else if (effect.kind === "cocktail_shortlist") {
      const ids = effect.drink_ids ?? [];
      let added = 0;
      for (const id of ids) {
        const cocktail = items.cocktails.get(id);
        if (!cocktail) continue;
        const result = addCocktail(next, cocktail);
        next = result.session;
        added += Number(result.added);
      }
      if (added) notices.push({ message: saved(added, "drink"), tone: "success" });
      if (added < ids.length && next.cocktailShortlist.length >= MAX_SHORTLIST) {
        notices.push({
          message: `Your shortlist holds up to ${MAX_SHORTLIST} drinks.`,
          tone: "info",
        });
      }
    } else if (effect.kind === "book_shortlist") {
      const ids = effect.book_ids ?? [];
      let added = 0;
      for (const id of ids) {
        const book = items.books.get(id);
        if (!book) continue;
        const result = addBook(next, book);
        next = result.session;
        added += Number(result.added);
      }
      if (added) {
        notices.push({
          message: `Added ${added} ${added === 1 ? "book" : "books"} to your reading list.`,
          tone: "success",
        });
      }
      if (added < ids.length && next.readingList.length >= MAX_SHORTLIST) {
        notices.push({
          message: `Your reading list holds up to ${MAX_SHORTLIST} books.`,
          tone: "info",
        });
      }
    } else if (effect.kind === "preferences" && effect.preferences) {
      const result = rememberPreferences(next, effect.preferences);
      next = result.session;
      notices.push(...memoryNotices(result.dropped));
    } else if (effect.kind === "cocktail_preferences" && effect.cocktail_preferences) {
      const result = rememberTastes(next, effect.cocktail_preferences);
      next = result.session;
      notices.push(...memoryNotices(result.dropped));
    } else if (effect.kind === "book_preferences" && effect.book_preferences) {
      const result = rememberReadingTastes(next, effect.book_preferences);
      next = result.session;
      notices.push(...memoryNotices(result.dropped));
    } else if (effect.kind === "evening") {
      const names: string[] = [];
      for (const id of effect.product_ids ?? []) {
        const product = items.products.get(id);
        if (!product) continue;
        const result = addProduct(next, product);
        next = result.session;
        if (result.added) names.push(productName(product));
      }
      for (const id of effect.book_ids ?? []) {
        const book = items.books.get(id);
        if (!book) continue;
        const result = addBook(next, book);
        next = result.session;
        if (result.added) names.push(book.title);
      }
      for (const id of effect.drink_ids ?? []) {
        const cocktail = items.cocktails.get(id);
        if (!cocktail) continue;
        const result = addCocktail(next, cocktail);
        next = result.session;
        if (result.added) names.push(cocktail.name);
      }
      notices.push(
        names.length
          ? { message: `Saved the evening: ${names.join(", ")}.`, tone: "success" }
          : { message: "Everything was already saved.", tone: "info" },
      );
    }
  }
  return { session: next, notices };
}
