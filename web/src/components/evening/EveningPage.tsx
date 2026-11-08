import { useMutation } from "@tanstack/react-query";
import { Info, Moon } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useId, useState } from "react";

import { postJson } from "../../api/client";
import { useServed } from "../../api/queries";
import type { EveningPlan, EveningRequest, EveningSwap, OutfitSlot } from "../../api/types";
import { useStores } from "../../state/stores";
import { useStore } from "../../state/store";
import { eveningTastes } from "../../state/session";
import { Button } from "../../ui/Button";
import { Segmented } from "../../ui/Segmented";
import { Toggle } from "../../ui/Toggle";
import { useToast } from "../../ui/toast";
import { PageHeader } from "../shell/PageHeader";
import { EveningPlanView } from "./EveningPlanView";
import { OCCASIONS, type Part, type SwapMode } from "./plan";
import styles from "./EveningPage.module.css";
import { SaveEveningDialog } from "./SaveEveningDialog";

const AUDIENCES = [
  { value: "women", label: "Women" },
  { value: "men", label: "Men" },
] as const;

type Audience = (typeof AUDIENCES)[number]["value"];

export function EveningPage() {
  const stores = useStores();
  const plan = useStore(stores.plan, (p) => p);
  const toast = useToast();
  const served = useServed().get("evening");
  const [occasion, setOccasion] = useState("");
  const [useBudget, setUseBudget] = useState(false);
  const [budget, setBudget] = useState(100);
  const [audience, setAudience] = useState<Audience | null>(null);
  const [alcoholFree, setAlcoholFree] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planKey, setPlanKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [swapping, setSwapping] = useState<string | null>(null);
  const occasionId = useId();
  const budgetId = useId();

  const planning = useMutation({
    mutationFn: (body: EveningRequest) => postJson<EveningPlan>("/evening/plan", body),
    onSuccess: (result) => {
      stores.plan.set(result);
      setPlanKey((key) => key + 1);
    },
    onError: (failure) => {
      setError(failure.message);
    },
  });

  const swapping_ = useMutation({
    mutationFn: (body: EveningSwap) => postJson<EveningPlan>("/evening/swap", body),
    onSuccess: (result) => {
      stores.plan.set(result);
      if (result.change) toast(result.change, "info");
    },
    onError: (failure) => {
      toast(failure.message, "error");
    },
    onSettled: () => {
      setSwapping(null);
    },
  });

  function submit(event?: { preventDefault: () => void }) {
    event?.preventDefault();
    const text = occasion.trim();
    if (text.length < 2) {
      setError("Tell Curio the occasion first, e.g. “date night”.");
      return;
    }
    setError(null);
    const body: EveningRequest = {
      occasion: text.slice(0, 200),
      ...eveningTastes(stores.session.get()),
    };
    if (useBudget) body.budget = budget || 100;
    if (audience) body.audience = audience;
    if (alcoholFree) body.alcohol_free = true;
    planning.mutate(body);
  }

  function swap(part: Part, slot?: OutfitSlot["slot"], mode: SwapMode = "other") {
    if (!plan) return;
    const body: EveningSwap = { evening: plan.state, part, mode } as EveningSwap;
    if (slot) body.slot = slot;
    const tastes = eveningTastes(stores.session.get());
    if (Object.keys(tastes).length) body.tastes = tastes;
    setSwapping(slot ? `${part}:${slot}` : part);
    swapping_.mutate(body);
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Plan my evening"
        intro="Tell Curio the occasion. It picks one theme and plans a book, a drink paired with that book and, when fashion is served, an outfit, and says why they go together."
      />
      {served?.enabled && served.hint ? (
        <p className={styles.hint} role="note">
          <Info size={16} aria-hidden="true" />
          <span>{served.hint}</span>
        </p>
      ) : null}
      <form className={styles.form} onSubmit={submit} aria-label="Plan an evening">
        <div className={styles.chips} role="group" aria-label="Occasions">
          {OCCASIONS.map((chip) => (
            <Button
              key={chip}
              variant="pill"
              size="sm"
              pressed={occasion === chip}
              onClick={() => {
                setOccasion(chip);
                setError(null);
              }}
            >
              {chip}
            </Button>
          ))}
        </div>
        <div className={styles.field}>
          <label htmlFor={occasionId} className={styles.label}>
            Occasion
          </label>
          <input
            id={occasionId}
            className={styles.input}
            value={occasion}
            maxLength={200}
            placeholder="Describe the evening, e.g. “rainy Sunday in”"
            autoComplete="off"
            onChange={(event) => {
              setOccasion(event.target.value);
            }}
          />
        </div>
        <div className={styles.controls}>
          <div className={styles.budget}>
            <Toggle checked={useBudget} onChange={setUseBudget}>
              Outfit budget
            </Toggle>
            <label htmlFor={budgetId} className="visually-hidden">
              Budget (€)
            </label>
            <div className={styles.money} data-disabled={useBudget ? undefined : "true"}>
              <span aria-hidden="true">€</span>
              <input
                id={budgetId}
                type="number"
                inputMode="numeric"
                min={10}
                max={5000}
                step={10}
                value={budget}
                disabled={!useBudget}
                onChange={(event) => {
                  setBudget(Math.min(5000, Math.max(0, Number(event.target.value))));
                }}
              />
            </div>
          </div>
          <div className={styles.audience}>
            <span className={styles.controlLabel} id={`${budgetId}-for`}>
              Outfit for
            </span>
            <Segmented
              label="Outfit for"
              options={AUDIENCES}
              value={audience}
              allowNone
              size="sm"
              onChange={(value) => {
                setAudience(audience === value ? null : value);
              }}
            />
          </div>
          <Toggle checked={alcoholFree} onChange={setAlcoholFree}>
            Alcohol-free
          </Toggle>
        </div>
        <div className={styles.submit}>
          <Button
            type="submit"
            variant="primary"
            size="lg"
            icon={<Moon />}
            disabled={planning.isPending}
          >
            {planning.isPending ? "Planning…" : "Plan my evening"}
          </Button>
          {error ? (
            <p className={styles.error} role="alert">
              {error}
            </p>
          ) : null}
        </div>
      </form>
      <section className={styles.result} aria-label="Your evening" aria-busy={planning.isPending}>
        <AnimatePresence mode="wait" initial={false}>
          {plan ? (
            <motion.div
              key="plan"
              initial={{ opacity: 0 }}
              animate={{ opacity: planning.isPending ? 0.45 : 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
            >
              <EveningPlanView
                plan={plan}
                planKey={String(planKey)}
                actions={{
                  kind: "page",
                  swap,
                  busy: swapping,
                  save: () => {
                    setSaving(true);
                  },
                }}
              />
              <SaveEveningDialog
                plan={plan}
                open={saving}
                onClose={() => {
                  setSaving(false);
                }}
              />
            </motion.div>
          ) : (
            <motion.div key="empty" className={styles.empty} exit={{ opacity: 0 }}>
              <div className={styles.ghosts} aria-hidden="true">
                <span />
                <span />
                <span />
              </div>
              <p>Pick an occasion above to see a plan.</p>
            </motion.div>
          )}
        </AnimatePresence>
      </section>
    </div>
  );
}
