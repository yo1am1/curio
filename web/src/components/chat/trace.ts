import type { AgentStep } from "../../api/types";

export function seconds(steps: AgentStep[]): string {
  const total = steps.reduce((sum, step) => sum + step.duration_ms, 0);
  return `${(total / 1000).toFixed(1)}s`;
}

/** "How Curio worked · 2 actions · 0.4s": actions are tool calls and proposals. */
export function traceLabel(steps: AgentStep[]): string {
  const actions = steps.filter((s) => s.kind === "tool" || s.kind === "approval").length;
  return `How Curio worked · ${actions} ${actions === 1 ? "action" : "actions"} · ${seconds(steps)}`;
}
