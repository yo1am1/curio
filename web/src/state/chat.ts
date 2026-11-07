/** Chat transcripts per domain and the request payloads built from them (the Streamlit
 * client's `history_item` and `chat_payload`). */
import type {
  AgentStep,
  AssistantReply,
  ChatRequest,
  ChatTurn,
  Domain,
  PendingAction,
} from "../api/types";
import { autoApprove, buildContext, type Session } from "./session";
import { readStorage, writeStorage } from "./store";

export const CHAT_KEY = "curio.chat.v1";
const HISTORY_TURNS = 10;
const CONTENT_LIMIT = 4000;
const CARD_IDS = 5;

export interface ChatEntry {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** The API's reply, for an assistant turn. */
  reply?: AssistantReply;
  /** A proposal answered: "Approved." or "Declined." */
  resolution?: string;
  /** The API did not accept the last decision: nothing was applied, so it can be retried. */
  decisionError?: string;
}

export interface DomainChat {
  entries: ChatEntry[];
  /** While a turn runs: the user's message (or decision) and the steps streamed so far. */
  running: { message: string; steps: AgentStep[]; decision: boolean } | null;
  /** A message that failed to send, offered for retry. */
  failed: { message: string; error: string } | null;
}

export type ChatState = Record<Domain, DomainChat>;

export function emptyChat(): DomainChat {
  return { entries: [], running: null, failed: null };
}

export function emptyChats(): ChatState {
  return { fashion: emptyChat(), cocktails: emptyChat(), books: emptyChat(), evening: emptyChat() };
}

/** Restore stored transcripts; an interrupted turn is not resumed. */
export function loadChats(storage: Storage | undefined): ChatState {
  const raw = readStorage(storage, CHAT_KEY);
  const chats = emptyChats();
  if (!raw || typeof raw !== "object") return chats;
  for (const domain of Object.keys(chats) as Domain[]) {
    const entries = (raw as Record<string, { entries?: unknown }>)[domain]?.entries;
    if (Array.isArray(entries)) chats[domain] = { ...emptyChat(), entries: entries as ChatEntry[] };
  }
  return chats;
}

export function saveChats(storage: Storage | undefined, chats: ChatState): void {
  const kept = Object.fromEntries(
    Object.entries(chats).map(([domain, chat]) => [domain, { entries: chat.entries }]),
  );
  writeStorage(storage, CHAT_KEY, kept);
}

let counter = 0;
export function entryId(): string {
  counter += 1;
  return `${Date.now().toString(36)}-${counter}`;
}

/** One earlier message as the API takes it: the text, plus the IDs of the cards shown. */
export function historyItem(entry: ChatEntry): ChatTurn {
  const item: ChatTurn = { role: entry.role, content: entry.content.slice(0, CONTENT_LIMIT) };
  const reply = entry.reply;
  if (reply?.products?.length) {
    item.product_ids = reply.products.slice(0, CARD_IDS).map((p) => p.product_id);
  }
  if (reply?.cocktails?.length) {
    item.drink_ids = reply.cocktails.slice(0, CARD_IDS).map((c) => c.drink_id);
  }
  if (reply?.books?.length) {
    item.book_ids = reply.books.slice(0, CARD_IDS).map((b) => b.book_id);
  }
  return item;
}

/** Recent turns plus the client-held session state for `domain`. */
export function chatPayload(
  domain: Domain,
  entries: ChatEntry[],
  session: Session,
): Partial<ChatRequest> {
  const payload: Partial<ChatRequest> = {
    history: entries.slice(-HISTORY_TURNS).map(historyItem),
    ...buildContext(domain, session),
  };
  const policy = autoApprove(domain, session);
  if (policy.length) payload.auto_approve = policy;
  return payload;
}

export function messagePayload(
  domain: Domain,
  message: string,
  entries: ChatEntry[],
  session: Session,
): Partial<ChatRequest> {
  return { message, ...chatPayload(domain, entries, session) };
}

export function decisionPayload(
  domain: Domain,
  entries: ChatEntry[],
  session: Session,
  action: PendingAction,
  approved: boolean,
  editedArgs: Record<string, unknown> | null,
): Partial<ChatRequest> {
  return {
    ...chatPayload(domain, entries, session),
    decision: { action, approved, edited_args: editedArgs },
  };
}

/** The index of the latest turn's open proposal, if it has one. */
export function openProposal(chat: DomainChat): number | null {
  const index = chat.entries.length - 1;
  const last = chat.entries[index];
  if (!last || chat.running || !last.reply?.pending_action || last.resolution) return null;
  return index;
}
