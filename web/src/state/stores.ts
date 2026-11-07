import { createContext, useContext } from "react";

import type { EveningPlan } from "../api/types";
import { type ChatState, loadChatSession, loadChats, saveChats } from "./chat";
import { loadSession, saveSession, type Session } from "./session";
import {
  browserStorage,
  createStore,
  readStorage,
  type Store,
  useStore,
  writeStorage,
} from "./store";

const PLAN_KEY = "curio.evening-plan.v1";

export interface AppStores {
  session: Store<Session>;
  chats: Store<ChatState>;
  /** The Plan my evening page's current plan (the API's reply). */
  plan: Store<EveningPlan | null>;
  /** This tab's chat session id, sent with every chat request (`session_id`). */
  chatSession: string;
}

export function createAppStores(
  local: Storage | undefined = browserStorage("localStorage"),
  tab: Storage | undefined = browserStorage("sessionStorage"),
): AppStores {
  return {
    session: createStore(loadSession(local), (session) => {
      saveSession(local, session);
    }),
    chats: createStore(loadChats(tab), (chats) => {
      saveChats(tab, chats);
    }),
    plan: createStore((readStorage(tab, PLAN_KEY) as EveningPlan | null) ?? null, (plan) => {
      writeStorage(tab, PLAN_KEY, plan);
    }),
    chatSession: loadChatSession(tab),
  };
}

export const StoresContext = createContext<AppStores | null>(null);

export function useStores(): AppStores {
  const stores = useContext(StoresContext);
  if (!stores) throw new Error("useStores needs an <AppStateProvider>.");
  return stores;
}

export function useSession<R>(select: (session: Session) => R): R {
  return useStore(useStores().session, select);
}

export function useChats<R>(select: (chats: ChatState) => R): R {
  return useStore(useStores().chats, select);
}
