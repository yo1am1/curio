import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, SERVER_ERROR, getJson, postForm } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("postForm", () => {
  it("posts multipart form data and lets the browser set the boundary", async () => {
    const fetch = vi.fn(() => Promise.resolve(Response.json([{ score: 0.9 }])));
    vi.stubGlobal("fetch", fetch);
    const form = new FormData();
    form.append("file", new Blob(["jpeg"], { type: "image/jpeg" }), "dress.jpg");

    const hits = await postForm<{ score: number }[]>("/search/image?top_k=9", form);

    expect(hits).toEqual([{ score: 0.9 }]);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/search/image?top_k=9");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(form);
    const headers = init.headers as Record<string, string>;
    expect(headers.accept).toBe("application/json");
    expect(Object.keys(headers).map((h) => h.toLowerCase())).not.toContain("content-type");
    const file = (init.body as FormData).get("file") as File;
    expect(file.name).toBe("dress.jpg");
    expect(file.type).toBe("image/jpeg");
  });

  it("turns the API's explanation into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(Response.json({ detail: "Upload an image file." }, { status: 400 })),
      ),
    );
    const error = await postForm("/search/image", new FormData()).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).message).toBe("Upload an image file.");
  });
});

describe("server errors", () => {
  async function failure(response: Response): Promise<ApiError> {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(response)),
    );
    return (await getJson("/cocktails").catch((e: unknown) => e)) as ApiError;
  }

  it("never shows a 500's body, which may be a stack trace", async () => {
    const json = await failure(
      Response.json({ detail: "Traceback boom KeyError('x')" }, { status: 500 }),
    );
    expect(json.status).toBe(500);
    expect(json.message).toBe(SERVER_ERROR);
    const html = await failure(new Response("<html>Bad Gateway</html>", { status: 502 }));
    expect(html.message).toBe(SERVER_ERROR);
  });

  it("keeps the explanations the API writes for a failed provider or a busy assistant", async () => {
    const busy = await failure(
      Response.json({ detail: "The assistant is busy. Please try again." }, { status: 503 }),
    );
    expect(busy.message).toBe("The assistant is busy. Please try again.");
  });

  it("keeps a plain-text 4xx explanation", async () => {
    const error = await failure(new Response("Nope", { status: 404 }));
    expect(error.message).toBe("Nope");
  });
});
