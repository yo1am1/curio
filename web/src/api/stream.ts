/** The `/chat/stream` client: NDJSON lines, one `step` event per agent step, then `reply`. */
import { ApiError, readError } from "./client";
import type { AgentStep, AssistantReply, ChatRequest, StreamEvent } from "./types";

export const ASSISTANT_FAILED = "The assistant failed. Please try again.";
export const ASSISTANT_UNAVAILABLE = "The assistant is unavailable. Please try again.";

/** Parse a byte stream of newline-delimited JSON, yielding one value per non-blank line.
 * Lines may arrive split across chunks; a last line without a newline still counts. */
export async function* parseNdjson(stream: ReadableStream<Uint8Array>): AsyncGenerator {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) yield JSON.parse(line) as unknown;
        newline = buffer.indexOf("\n");
      }
      if (done) break;
    }
    const rest = buffer.trim();
    if (rest) yield JSON.parse(rest) as unknown;
  } finally {
    reader.releaseLock();
  }
}

function isStepEvent(event: unknown): event is Extract<StreamEvent, { type: "step" }> {
  return !!event && typeof event === "object" && (event as { type?: unknown }).type === "step";
}

/** Read a chat stream: report each step and return the final reply. An `error` event, a
 * stream that ends without a reply, or malformed JSON raise an `ApiError`. A line that is not
 * an event at all is a plain, non-streamed reply. */
export async function readChatStream(
  stream: ReadableStream<Uint8Array>,
  onStep: (step: AgentStep) => void,
): Promise<AssistantReply> {
  try {
    for await (const event of parseNdjson(stream)) {
      if (isStepEvent(event)) {
        onStep(event.step);
        continue;
      }
      const typed = event as Partial<StreamEvent> & Record<string, unknown>;
      if (typed.type === "reply") return (typed as { reply: AssistantReply }).reply;
      if (typed.type === "error") {
        const failure = typed as { status?: number; detail?: string };
        throw new ApiError(failure.status ?? 502, failure.detail ?? ASSISTANT_FAILED);
      }
      return typed as unknown as AssistantReply;
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, ASSISTANT_UNAVAILABLE);
  }
  throw new ApiError(0, ASSISTANT_UNAVAILABLE);
}

/** POST one chat turn to `/chat/stream`; `onStep` sees each step as the agent takes it. */
export async function streamChat(
  payload: Partial<ChatRequest>,
  onStep: (step: AgentStep) => void,
  signal?: AbortSignal,
): Promise<AssistantReply> {
  let response: Response;
  try {
    response = await fetch("/chat/stream", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/x-ndjson" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, ASSISTANT_UNAVAILABLE);
  }
  if (!response.ok) throw await readError(response);
  if (!response.body) throw new ApiError(0, ASSISTANT_UNAVAILABLE);
  return readChatStream(response.body, onStep);
}
