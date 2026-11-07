import { describe, expect, it } from "vitest";

import { step, streamOf } from "../test/fixtures";
import { ApiError } from "./client";
import { parseNdjson, readChatStream } from "./stream";

const reply = { text: "Hi", domain: "fashion", status: "complete", intent: { query: "" } };

async function all(stream: ReadableStream<Uint8Array>) {
  const values: unknown[] = [];
  for await (const value of parseNdjson(stream)) values.push(value);
  return values;
}

describe("parseNdjson", () => {
  it("joins lines split across chunks and skips blank lines", async () => {
    const values = await all(streamOf(['{"a":', '1}\n\n{"b"', ":2}\n", "  \n", '{"c":3}']));
    expect(values).toEqual([{ a: 1 }, { b: 2 }, { c: 3 }]);
  });

  it("decodes multi-byte characters split between chunks", async () => {
    const bytes = new TextEncoder().encode('{"price":"€30"}\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 11));
        controller.enqueue(bytes.slice(11));
        controller.close();
      },
    });
    expect(await all(stream)).toEqual([{ price: "€30" }]);
  });
});

describe("readChatStream", () => {
  it("reports each step, then returns the reply", async () => {
    const steps: unknown[] = [];
    const first = step({ kind: "plan", name: "Understand request" });
    const lines = [
      JSON.stringify({ type: "step", step: first }) + "\n",
      JSON.stringify({ type: "step", step: step() }) + "\n",
      JSON.stringify({ type: "reply", reply }) + "\n",
    ];
    const result = await readChatStream(streamOf(lines), (s) => steps.push(s));
    expect(steps).toEqual([first, step()]);
    expect(result.text).toBe("Hi");
  });

  it("raises an error event as an ApiError with the server's detail", async () => {
    const lines = [
      JSON.stringify({ type: "error", status: 429, detail: "The assistant is busy." }),
    ];
    const failure = readChatStream(streamOf(lines), () => undefined);
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({ status: 429, message: "The assistant is busy." });
  });

  it("accepts a plain, non-streamed reply", async () => {
    const result = await readChatStream(streamOf([JSON.stringify(reply)]), () => undefined);
    expect(result.text).toBe("Hi");
  });

  it("fails when the stream ends without a reply or is malformed", async () => {
    const steps = [JSON.stringify({ type: "step", step: step() }) + "\n"];
    await expect(readChatStream(streamOf(steps), () => undefined)).rejects.toMatchObject({
      message: "The assistant is unavailable. Please try again.",
    });
    await expect(readChatStream(streamOf(["{not json\n"]), () => undefined)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
