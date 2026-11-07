import { Info, RotateCcw, SquarePen } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef } from "react";
import { Navigate, useLocation, useNavigate, useParams } from "react-router";

import { useHealth, useServed } from "../../api/queries";
import type { Domain } from "../../api/types";
import { DOMAINS, LLM_HINT, isDomain } from "../../domains/registry";
import { useChats } from "../../state/stores";
import { useChatActions } from "../../state/actions";
import { openProposal } from "../../state/chat";
import { Button } from "../../ui/Button";
import { SessionPanel } from "../session/SessionPanel";
import { PageHeader } from "../shell/PageHeader";
import { ApprovalDrawer } from "./ApprovalDrawer";
import styles from "./ChatPage.module.css";
import { Composer } from "./Composer";
import { Markdown } from "./Markdown";
import { AssistantTurn, RunningTurn, UserTurn } from "./Turn";

export function ChatRoute() {
  const { domain } = useParams();
  const served = useServed();
  if (!isDomain(domain) || !served.get(domain)?.enabled) return <Navigate to="/" replace />;
  return <ChatPage key={domain} domain={domain} />;
}

function Welcome({ domain, send }: { domain: Domain; send: (text: string) => void }) {
  const copy = DOMAINS[domain].copy;
  return (
    <motion.div
      className={styles.welcome}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <span className={styles.avatar} aria-hidden="true">
        C
      </span>
      <div className={styles.welcomeBody}>
        <Markdown>{copy.welcome}</Markdown>
        <div className={styles.starters} role="group" aria-label="Try one of these">
          {copy.starters.map((starter) => (
            <Button
              key={starter}
              variant="pill"
              size="sm"
              onClick={() => {
                send(starter);
              }}
            >
              {starter}
            </Button>
          ))}
        </div>
      </div>
    </motion.div>
  );
}

export function ChatPage({ domain }: { domain: Domain }) {
  const chat = useChats((chats) => chats[domain]);
  const actions = useChatActions(domain);
  const info = useServed().get(domain);
  const health = useHealth();
  const location = useLocation();
  const navigate = useNavigate();
  const copy = DOMAINS[domain].copy;
  const latestRef = useRef<HTMLDivElement>(null);
  const chatEnabled = !!info?.chat_enabled;
  const proposal = openProposal(chat);
  const busy = !!chat.running;
  const decisionError = proposal !== null ? chat.entries[proposal]?.decisionError : undefined;

  // A hand-off ("Ask Curio about it" in a dialog) arrives as router state: send it once.
  const handoff = (location.state as { ask?: string } | null)?.ask;
  useEffect(() => {
    if (!handoff || !chatEnabled) return;
    void navigate(location.pathname, { replace: true, state: null });
    actions.send(handoff);
  }, [handoff, chatEnabled, actions, navigate, location.pathname]);

  // Bring the newest turn (or the live trace) into view.
  const turns = chat.entries.length + (chat.running ? 1 : 0);
  useEffect(() => {
    if (turns === 0) return;
    latestRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns]);

  const userQuestion = (index: number) =>
    chat.entries
      .slice(0, index)
      .reverse()
      .find((entry) => entry.role === "user")?.content ?? "";

  return (
    <div className={styles.page}>
      <PageHeader
        title={copy.title}
        documentTitle={`Ask Curio · ${DOMAINS[domain].label}`}
        intro={copy.intro}
        actions={
          chat.entries.length ? (
            <Button variant="ghost" size="sm" icon={<SquarePen />} onClick={actions.reset}>
              New conversation
            </Button>
          ) : undefined
        }
      />
      <div className={styles.layout}>
        <div className={styles.conversation}>
          {!chatEnabled ? (
            <div className={styles.unavailable} role="note">
              <Info size={18} aria-hidden="true" />
              <div>
                <p>{copy.unavailable}</p>
                {health.data?.llm_configured === false ? (
                  <p className={styles.fine}>To enable chat, {LLM_HINT}</p>
                ) : null}
              </div>
            </div>
          ) : (
            <>
              <div className={styles.transcript} aria-label="Conversation" role="log">
                {chat.entries.length === 0 && !chat.running ? (
                  <Welcome domain={domain} send={actions.send} />
                ) : null}
                {chat.entries.map((entry, index) => {
                  const last = index === chat.entries.length - 1;
                  const anchor = last && !chat.running ? latestRef : undefined;
                  return (
                    <div key={entry.id} ref={anchor} className={styles.turn}>
                      {entry.role === "user" ? (
                        <UserTurn text={entry.content} />
                      ) : (
                        <AssistantTurn
                          entry={entry}
                          domain={domain}
                          latest={last && !chat.running}
                          openProposal={index === proposal}
                          question={userQuestion(index)}
                          send={actions.send}
                        />
                      )}
                    </div>
                  );
                })}
                {chat.running ? (
                  <div ref={latestRef} className={styles.turn}>
                    <UserTurn text={chat.running.message} pending />
                    <RunningTurn steps={chat.running.steps} />
                  </div>
                ) : null}
                {chat.failed ? (
                  <div className={styles.failed} role="alert">
                    <UserTurn text={chat.failed.message} />
                    <div className={styles.failure}>
                      <p>{chat.failed.error}</p>
                      <Button
                        size="sm"
                        icon={<RotateCcw />}
                        onClick={() => {
                          const message = chat.failed?.message ?? "";
                          actions.dismissFailure();
                          actions.send(message);
                        }}
                      >
                        Send again
                      </Button>
                    </div>
                  </div>
                ) : null}
              </div>
              <div className={styles.dock}>
                <AnimatePresence>
                  {proposal !== null || (busy && chat.running?.decision)
                    ? (() => {
                        const index = proposal ?? chat.entries.length - 1;
                        const action = chat.entries[index]?.reply?.pending_action;
                        if (!action) return null;
                        return (
                          <ApprovalDrawer
                            key={action.id}
                            action={action}
                            busy={busy}
                            error={decisionError}
                            onDecide={(approved, edits) => {
                              actions.decide(index, approved, edits);
                            }}
                          />
                        );
                      })()
                    : null}
                </AnimatePresence>
                <Composer placeholder={copy.placeholder} busy={busy} onSend={actions.send} />
              </div>
            </>
          )}
        </div>
        <SessionPanel domain={domain} onAsk={actions.send} />
      </div>
    </div>
  );
}
