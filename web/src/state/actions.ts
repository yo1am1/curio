/** Session and chat actions with their side effects: API calls and toasts. */
import { useCallback, useMemo, useRef } from "react";

import { ApiError, getJson } from "../api/client";
import { streamChat } from "../api/stream";
import type { AssistantReply, Book, Cocktail, Domain, PendingAction, Product } from "../api/types";
import { MAX_SHORTLIST, type PolicySetting } from "../domains/registry";
import { productName } from "../domains/format";
import { useToast } from "../ui/toast";
import { useStores } from "./stores";
import {
  type ChatEntry,
  type DomainChat,
  decisionPayload,
  emptyChat,
  entryId,
  messagePayload,
} from "./chat";
import {
  addBook,
  addCocktail,
  addProduct,
  applyEffects,
  effectItems,
  type Fetchers,
  forget,
  type MemoryChip,
  removeBook,
  removeCocktail,
  removeProduct,
} from "./session";

/** A decision whose reply was lost (the connection dropped): the server may have applied it. */
export const DECISION_LOST =
  "The reply to your decision was lost, so it may already have been applied. Ask again to see where things stand.";

const fetchOrNull =
  <T>(path: (id: number) => string) =>
  (id: number): Promise<T | null> =>
    getJson<T>(path(id)).catch(() => null);

export const FETCHERS: Fetchers = {
  product: fetchOrNull<Product>((id) => `/products/${id}`),
  cocktail: fetchOrNull<Cocktail>((id) => `/cocktails/${id}`),
  book: fetchOrNull<Book>((id) => `/books/${id}`),
};

export function useSessionActions() {
  const { session } = useStores();
  const toast = useToast();
  return useMemo(
    () => ({
      saveProduct: (product: Product) => {
        const result = addProduct(session.get(), product);
        session.set(result.session);
        if (result.added) toast(`Saved ${productName(product)} to your shortlist.`);
        else if (result.full) toast(`Your shortlist holds up to ${MAX_SHORTLIST} pieces.`, "info");
      },
      saveCocktail: (cocktail: Cocktail) => {
        const result = addCocktail(session.get(), cocktail);
        session.set(result.session);
        if (result.added) toast(`Saved ${cocktail.name} to your shortlist.`);
        else if (result.full) toast(`Your shortlist holds up to ${MAX_SHORTLIST} drinks.`, "info");
      },
      saveBook: (book: Book) => {
        const result = addBook(session.get(), book);
        session.set(result.session);
        if (result.added) toast(`Added ${book.title} to your reading list.`);
        else if (result.full)
          toast(`Your reading list holds up to ${MAX_SHORTLIST} books.`, "info");
      },
      removeProduct: (id: number) => {
        session.set((s) => removeProduct(s, id));
      },
      removeCocktail: (id: number) => {
        session.set((s) => removeCocktail(s, id));
      },
      removeBook: (id: number) => {
        session.set((s) => removeBook(s, id));
      },
      forget: (chip: MemoryChip) => {
        session.set((s) => forget(s, chip));
      },
      setPolicy: (setting: PolicySetting, ask: boolean) => {
        session.set((s) => ({ ...s, policy: { ...s.policy, [setting]: ask } }));
      },
      setShopper: (id: number | null) => {
        session.set((s) => ({ ...s, shopperId: id }));
      },
      /** Apply a reply's approved effects to the lists and memory, and say what changed. */
      applyReply: async (
        reply: Pick<AssistantReply, "applied" | "products" | "cocktails" | "books">,
      ) => {
        if (!reply.applied?.length) return;
        const items = await effectItems(reply, FETCHERS);
        const result = applyEffects(session.get(), reply, items);
        session.set(result.session);
        for (const notice of result.notices) toast(notice.message, notice.tone);
      },
    }),
    [session, toast],
  );
}

/** The chat of one domain: send a message, answer a proposal, start over. */
export function useChatActions(domain: Domain) {
  const { chats, session, chatSession } = useStores();
  const actions = useSessionActions();
  const controller = useRef<AbortController | null>(null);

  const update = useCallback(
    (change: (chat: DomainChat) => DomainChat) => {
      chats.set((all) => ({ ...all, [domain]: change(all[domain]) }));
    },
    [chats, domain],
  );

  const finish = useCallback(
    async (reply: AssistantReply, user: ChatEntry, resolve?: number) => {
      await actions.applyReply(reply);
      // Follow-ups ("cheaper shoes", "another book", "save it") refer to the latest plan.
      if (reply.outfit) session.set((s) => ({ ...s, outfit: reply.outfit?.state ?? null }));
      if (reply.evening) session.set((s) => ({ ...s, evening: reply.evening?.state ?? null }));
      const assistant: ChatEntry = { id: entryId(), role: "assistant", content: reply.text, reply };
      update((chat) => ({
        ...chat,
        running: null,
        failed: null,
        entries: [
          ...chat.entries.map((entry, index) =>
            index === resolve
              ? {
                  ...entry,
                  resolution: user.content === "Approved" ? "Approved." : "Declined.",
                  decisionError: undefined,
                }
              : entry,
          ),
          user,
          assistant,
        ],
      }));
    },
    [actions, session, update],
  );

  const run = useCallback(
    async (
      payload: ReturnType<typeof messagePayload>,
      user: ChatEntry,
      resolve?: number,
    ): Promise<boolean> => {
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      update((chat) => ({
        ...chat,
        failed: null,
        running: { message: user.content, steps: [], decision: resolve !== undefined },
      }));
      try {
        const reply = await streamChat(
          payload,
          (step) => {
            update((chat) =>
              chat.running
                ? { ...chat, running: { ...chat.running, steps: [...chat.running.steps, step] } }
                : chat,
            );
          },
          abort.signal,
        );
        await finish(reply, user, resolve);
        return true;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return false;
        const message =
          error instanceof ApiError ? error.message : "The assistant failed. Please try again.";
        update((chat) => {
          if (resolve === undefined) {
            return { ...chat, running: null, failed: { message: user.content, error: message } };
          }
          // 409: the proposal was already answered (perhaps by this very decision, whose reply
          // was lost), so deciding again cannot work. A lost connection: it may have been.
          const stale = error instanceof ApiError && error.status === 409;
          const lost = error instanceof ApiError && error.status === 0;
          return {
            ...chat,
            running: null,
            entries: chat.entries.map((entry, index) =>
              index === resolve
                ? {
                    ...entry,
                    decisionError: lost ? DECISION_LOST : message,
                    decisionStale: stale || entry.decisionStale,
                  }
                : entry,
            ),
          };
        });
        return false;
      }
    },
    [finish, update],
  );

  return useMemo(
    () => ({
      send: (text: string) => {
        const message = text.trim().slice(0, 2000);
        const chat = chats.get()[domain];
        if (!message || chat.running) return;
        const payload = messagePayload(domain, message, chat.entries, session.get(), chatSession);
        void run(payload, { id: entryId(), role: "user", content: message });
      },
      decide: (index: number, approved: boolean, edits: Record<string, unknown> | null) => {
        const chat = chats.get()[domain];
        const action: PendingAction | null | undefined = chat.entries[index]?.reply?.pending_action;
        if (!action || chat.running) return;
        const payload = decisionPayload(
          domain,
          chat.entries,
          session.get(),
          action,
          approved,
          approved ? edits : null,
          chatSession,
        );
        const user: ChatEntry = {
          id: entryId(),
          role: "user",
          content: approved ? "Approved" : "Declined",
        };
        void run(payload, user, index);
      },
      reset: () => {
        controller.current?.abort();
        update(() => emptyChat());
        if (domain === "fashion") session.set((s) => ({ ...s, outfit: null }));
        if (domain === "evening") session.set((s) => ({ ...s, evening: null }));
      },
      dismissFailure: () => {
        update((chat) => ({ ...chat, failed: null }));
      },
    }),
    [chatSession, chats, domain, run, session, update],
  );
}
