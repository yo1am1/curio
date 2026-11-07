import { ArrowRight, Check, ShieldQuestion, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";

import { postJson } from "../../api/client";
import type { AgentStep, Domain } from "../../api/types";
import type { ChatEntry } from "../../state/chat";
import { Button } from "../../ui/Button";
import { useToast } from "../../ui/toast";
import { BookCard, CocktailCard, ItemGrid, ProductCard } from "../cards/ItemCards";
import { OutfitCard } from "../cards/OutfitCard";
import { PairingCard } from "../cards/PairingCard";
import { EveningPlanView } from "../evening/EveningPlanView";
import { GroundingBadge } from "./GroundingBadge";
import { Markdown } from "./Markdown";
import { StepTrace } from "./StepTrace";
import styles from "./Turn.module.css";

const turnMotion = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  transition: { type: "spring", stiffness: 300, damping: 30 },
} as const;

export function UserTurn({ text, pending = false }: { text: string; pending?: boolean }) {
  return (
    <motion.div className={styles.user} {...turnMotion} data-pending={pending ? "true" : undefined}>
      <p className="visually-hidden">You said:</p>
      <div className={styles.bubble}>{text}</div>
    </motion.div>
  );
}

function Avatar() {
  return (
    <span className={styles.avatar} aria-hidden="true">
      C
    </span>
  );
}

export function RunningTurn({ steps }: { steps: AgentStep[] }) {
  return (
    <motion.div className={styles.assistant} {...turnMotion} data-testid="running-turn">
      <Avatar />
      <div className={styles.body}>
        <StepTrace steps={steps} live />
      </div>
    </motion.div>
  );
}

function Feedback({ entry, question }: { entry: ChatEntry; question: string }) {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const toast = useToast();
  const reply = entry.reply;
  if (!reply) return null;
  const rate = (value: "up" | "down") => {
    setRating(value);
    void postJson("/chat/feedback", {
      rating: value,
      message: question.slice(0, 2000),
      answer: entry.content.slice(0, 4000),
      product_ids: (reply.products ?? []).slice(0, 5).map((p) => p.product_id),
      tools: (reply.steps ?? [])
        .filter((s) => s.kind === "tool")
        .map((s) => s.name)
        .slice(0, 12),
    })
      .then(() => {
        toast("Thanks for the feedback.");
      })
      .catch(() => {
        setRating(null);
      });
  };
  return (
    <div className={styles.feedback} role="group" aria-label="Rate this answer">
      <Button
        variant="ghost"
        size="sm"
        icon={<ThumbsUp fill={rating === "up" ? "currentColor" : "none"} />}
        aria-label="Helpful"
        pressed={rating === "up"}
        disabled={rating !== null}
        onClick={() => {
          rate("up");
        }}
      />
      <Button
        variant="ghost"
        size="sm"
        icon={<ThumbsDown fill={rating === "down" ? "currentColor" : "none"} />}
        aria-label="Not helpful"
        pressed={rating === "down"}
        disabled={rating !== null}
        onClick={() => {
          rate("down");
        }}
      />
    </div>
  );
}

/** A proposal in the transcript: its summary and what became of it. The open proposal is
 * answered in the approval drawer below the conversation. */
function ProposalNote({ entry, open }: { entry: ChatEntry; open: boolean }) {
  const action = entry.reply?.pending_action;
  if (!action) return null;
  if (open) {
    return (
      <p className={styles.waiting}>
        <ShieldQuestion size={15} aria-hidden="true" />
        Waiting for your approval below.
      </p>
    );
  }
  const status = entry.resolution ?? "No longer pending.";
  const Icon =
    entry.resolution === "Approved."
      ? Check
      : entry.resolution === "Declined."
        ? X
        : ShieldQuestion;
  return (
    <div className={styles.proposal} data-state={entry.resolution ? "resolved" : "stale"}>
      <Icon size={16} aria-hidden="true" />
      <div>
        <p className={styles.proposalTitle}>Needs your approval</p>
        {action.summary ? <p className={styles.proposalSummary}>{action.summary}</p> : null}
        <p className={styles.proposalStatus}>{status}</p>
      </div>
    </div>
  );
}

export function AssistantTurn({
  entry,
  domain,
  latest,
  openProposal,
  question,
  send,
}: {
  entry: ChatEntry;
  domain: Domain;
  latest: boolean;
  openProposal: boolean;
  question: string;
  send: (message: string) => void;
}) {
  const reply = entry.reply;
  const evening = reply?.evening;
  const outfit = reply?.outfit;
  const worn = new Set(
    (outfit?.slots ?? []).flatMap((s) => (s.product ? [s.product.product_id] : [])),
  );
  const products = evening ? [] : (reply?.products ?? []).filter((p) => !worn.has(p.product_id));
  const clarification = latest ? reply?.clarification : null;
  const suggestions = latest ? (reply?.suggestions ?? []) : [];
  return (
    <motion.article
      className={styles.assistant}
      {...turnMotion}
      aria-label="Curio's answer"
      data-testid="assistant-turn"
    >
      <Avatar />
      <div className={styles.body}>
        {reply?.steps?.length ? <StepTrace steps={reply.steps} /> : null}
        <Markdown>{entry.content}</Markdown>
        <GroundingBadge domain={domain} grounding={reply?.grounding} />
        {evening ? (
          <EveningPlanView
            plan={evening}
            said={entry.content}
            actions={{ kind: "chat", send, active: latest }}
          />
        ) : null}
        {outfit ? <OutfitCard outfit={outfit} active={latest} send={send} /> : null}
        {products.length ? (
          <ItemGrid label="Pieces in this answer">
            {products.map((product) => (
              <ProductCard key={product.product_id} product={product} />
            ))}
          </ItemGrid>
        ) : null}
        {!evening && reply?.pairing ? (
          <div className={styles.panel}>
            <PairingCard result={reply.pairing} />
          </div>
        ) : null}
        {!evening && !reply?.pairing && reply?.cocktails?.length ? (
          <ItemGrid label="Drinks in this answer" kind="cocktail">
            {reply.cocktails.map((cocktail) => (
              <CocktailCard key={cocktail.drink_id} cocktail={cocktail} />
            ))}
          </ItemGrid>
        ) : null}
        {!evening && reply?.books?.length ? (
          <ItemGrid label="Books in this answer" kind="book">
            {reply.books.map((book) => (
              <BookCard key={book.book_id} book={book} />
            ))}
          </ItemGrid>
        ) : null}
        <ProposalNote entry={entry} open={openProposal} />
        {clarification?.options.length ? (
          <div className={styles.options} role="group" aria-label={clarification.question}>
            {clarification.options.map((option) => (
              <Button
                key={option}
                variant="pill"
                size="sm"
                onClick={() => {
                  send(option);
                }}
              >
                {option}
              </Button>
            ))}
          </div>
        ) : null}
        {suggestions.length ? (
          <div className={styles.suggestions} role="group" aria-label="Suggested follow-ups">
            {suggestions.map((suggestion) => (
              <Button
                key={suggestion}
                variant="link"
                size="sm"
                iconEnd={<ArrowRight />}
                onClick={() => {
                  send(suggestion);
                }}
              >
                {suggestion}
              </Button>
            ))}
          </div>
        ) : null}
        {reply?.steps?.length && reply.status === "complete" ? (
          <Feedback entry={entry} question={question} />
        ) : null}
      </div>
    </motion.article>
  );
}
