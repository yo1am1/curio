import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { product, proposal, step } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { ChatPage } from "./ChatPage";

const DOMAINS = [
  { id: "fashion", label: "Fashion", enabled: true, chat_enabled: true, item_count: 3 },
];

function ndjson(...events: unknown[]): Response {
  return new Response(events.map((e) => JSON.stringify(e)).join("\n") + "\n", {
    headers: { "content-type": "application/x-ndjson" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatPage", () => {
  it("streams a turn, then approves a narrowed proposal and applies the effect", async () => {
    const pieces = [product(1), product(2)];
    const action = proposal("SaveToShortlist", { product_ids: [1, 2] }, { products: pieces });
    const bodies: Record<string, unknown>[] = [];
    const replies = [
      ndjson(
        { type: "step", step: step({ kind: "plan", name: "Understand request" }) },
        {
          type: "reply",
          reply: {
            text: "I'd like to save these pieces.",
            domain: "fashion",
            status: "needs_approval",
            intent: { query: "save" },
            products: pieces,
            steps: [step({ kind: "approval", name: "SaveToShortlist", status: "pending" })],
            pending_action: action,
          },
        },
      ),
      ndjson({
        type: "reply",
        reply: {
          text: "Done: it is saved.",
          domain: "fashion",
          status: "complete",
          intent: { query: "save" },
          applied: [{ kind: "shortlist", product_ids: [2] }],
          grounding: { status: "verified", checked: 1, issues: [], counts: { price: 1 } },
        },
      }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url === "/chat/stream") {
          bodies.push(JSON.parse(init?.body as string) as Record<string, unknown>);
          return Promise.resolve(replies.shift());
        }
        const body =
          url === "/domains"
            ? DOMAINS
            : url === "/products/2"
              ? product(2)
              : url.startsWith("/demo")
                ? []
                : {};
        return Promise.resolve(new Response(JSON.stringify(body)));
      }),
    );
    const { stores } = renderApp(<ChatPage domain="fashion" />);
    const box = await screen.findByLabelText("Message Curio");
    await userEvent.type(box, "Save the first two{Enter}");
    const drawer = await screen.findByTestId("approval-drawer");
    expect(bodies[0]).toMatchObject({ message: "Save the first two", history: [] });
    // The tab's session id goes with every turn; the proposal is signed over it.
    expect(bodies[0]?.session_id).toBe(stores.chatSession);
    expect(stores.chatSession).toMatch(/^[0-9a-f]{32}$/);
    await userEvent.click(within(drawer).getByRole("checkbox", { name: "Piece 1 · €19.99" }));
    await userEvent.click(within(drawer).getByRole("button", { name: "Approve" }));
    await screen.findByText("Done: it is saved.");
    expect(bodies[1]).toMatchObject({
      decision: { action, approved: true, edited_args: { product_ids: [2] } },
      history: [
        { role: "user", content: "Save the first two" },
        { role: "assistant", content: "I'd like to save these pieces.", product_ids: [1, 2] },
      ],
    });
    expect(bodies[1]).not.toHaveProperty("message");
    expect(bodies[1]?.session_id).toBe(stores.chatSession);
    await waitFor(() => {
      expect(stores.session.get().shortlist.map((p) => p.product_id)).toEqual([2]);
    });
    expect(screen.getByText("Approved.")).toBeInTheDocument();
    expect(screen.getByTestId("grounding-badge")).toHaveTextContent(
      "Checked 1 price against the catalog",
    );
  });

  it("after a lost decision reply and a 409, offers only to ask again", async () => {
    const pieces = [product(1), product(2)];
    const action = proposal("SaveToShortlist", { product_ids: [1, 2] }, { products: pieces });
    const proposing = () =>
      ndjson({
        type: "reply",
        reply: {
          text: "I'd like to save these pieces.",
          domain: "fashion",
          status: "needs_approval",
          intent: { query: "save" },
          products: pieces,
          steps: [step({ kind: "approval", name: "SaveToShortlist", status: "pending" })],
          pending_action: action,
        },
      });
    const bodies: Record<string, unknown>[] = [];
    const replies: (() => Promise<Response>)[] = [
      () => Promise.resolve(proposing()),
      // The server applied the decision, but the connection dropped before the reply.
      () => Promise.reject(new TypeError("Failed to fetch")),
      // Deciding again: the proposal was already answered.
      () =>
        Promise.resolve(
          ndjson({
            type: "error",
            status: 409,
            detail: "This proposal was already answered. Please ask again.",
          }),
        ),
      () => Promise.resolve(proposing()),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url === "/chat/stream") {
          bodies.push(JSON.parse(init?.body as string) as Record<string, unknown>);
          const next = replies.shift();
          return next ? next() : Promise.reject(new Error("no more replies"));
        }
        const body = url === "/domains" ? DOMAINS : url.startsWith("/demo") ? [] : {};
        return Promise.resolve(new Response(JSON.stringify(body)));
      }),
    );
    renderApp(<ChatPage domain="fashion" />);
    const box = await screen.findByLabelText("Message Curio");
    await userEvent.type(box, "Save the first two{Enter}");
    const drawer = await screen.findByTestId("approval-drawer");

    await userEvent.click(within(drawer).getByRole("button", { name: "Approve" }));
    // A lost reply: the decision may already have been applied.
    expect(await within(drawer).findByRole("alert")).toHaveTextContent(
      "may already have been applied",
    );
    expect(within(drawer).getByRole("button", { name: "Approve" })).toBeEnabled();

    await userEvent.click(within(drawer).getByRole("button", { name: "Approve" }));
    expect(await within(drawer).findByRole("alert")).toHaveTextContent(
      "This proposal was already answered. Please ask again.",
    );
    // Stale: no Approve or Decline that can only fail again, just "Ask again".
    expect(within(drawer).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(drawer).queryByRole("button", { name: "Decline" })).toBeNull();
    for (const box of within(drawer).getAllByRole("checkbox")) expect(box).toBeDisabled();

    await userEvent.click(within(drawer).getByRole("button", { name: "Ask again" }));
    await waitFor(() => {
      expect(bodies).toHaveLength(4);
    });
    expect(bodies[3]).toMatchObject({ message: "Save the first two" });
    expect(bodies[3]).not.toHaveProperty("decision");
    // The new turn proposes afresh; the old proposal is no longer pending.
    expect(await screen.findByText("No longer pending.")).toBeInTheDocument();
    const fresh = await screen.findByTestId("approval-drawer");
    expect(within(fresh).getByRole("button", { name: "Approve" })).toBeEnabled();
  });
});
