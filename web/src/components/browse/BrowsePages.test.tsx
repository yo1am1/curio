import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { cocktail, product } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { CocktailSearchPage } from "../cocktails/CocktailSearchPage";
import { DiscoverPage } from "../fashion/DiscoverPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

type Call = { url: string; init?: RequestInit };

/** A fetch stub answering by path; every call is recorded. */
function api(routes: Record<string, unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const path = url.split("?")[0] ?? url;
      if (path.endsWith("/image") && !init?.method) {
        // jsdom's FormData takes only jsdom's Blob, so the photo is one.
        const photo = new Blob(["jpeg"], { type: "image/jpeg" });
        return Promise.resolve({ ok: true, blob: () => Promise.resolve(photo) });
      }
      if (!(path in routes)) return Promise.resolve(new Response("{}", { status: 404 }));
      return Promise.resolve(Response.json(routes[path]));
    }),
  );
  return calls;
}

describe("Cocktail search", () => {
  it("sends the filters as the request body and shows the matches", async () => {
    const calls = api({
      "/health": { cocktail_count: 425 },
      "/cocktails/ingredients": ["Gin", "Lime", "Mint"],
      "/cocktails/categories": ["Cocktail", "Shot"],
      "/cocktails/search": [
        { cocktail: cocktail(7, { name: "Mojito" }), score: 1, reasons: ["Contains lime"] },
      ],
    });
    renderApp(<CocktailSearchPage />);
    expect(await screen.findByText(/Search 425 recipes/)).toBeInTheDocument();
    const box = screen.getByRole("combobox", { name: "Must include" });
    await userEvent.type(box, "lim{Enter}{Escape}");
    await userEvent.click(screen.getByRole("radio", { name: "Alcohol-free" }));
    await userEvent.selectOptions(screen.getByLabelText("Category"), "Cocktail");
    await userEvent.type(screen.getByLabelText("Describe a drink"), "minty{Enter}");

    const results = await screen.findByRole("region", { name: "Matching drinks" });
    expect(await within(results).findByText("Mojito")).toBeInTheDocument();
    expect(within(results).getByText("Contains lime")).toBeInTheDocument();
    const searches = calls.filter((c) => c.url === "/cocktails/search");
    expect(JSON.parse(searches[searches.length - 1]?.init?.body as string)).toEqual({
      query: "minty",
      ingredients: ["Lime"],
      alcohol: "non_alcoholic",
      category: "Cocktail",
      exclude_ingredients: [],
      top_k: 12,
    });
  });
});

describe("Photo search", () => {
  it("uploads an example photo as multipart form data", async () => {
    const calls = api({
      "/sections": ["Ladieswear"],
      "/search/examples": [
        { photo_id: 714582004, name: "Totem (1)", product_type: "Sweater", colour: "Grey" },
      ],
      "/search/image": [{ product: product(3, { display_name: "Grey Jumper" }), score: 0.9 }],
    });
    renderApp(<DiscoverPage />);
    await userEvent.click(screen.getByRole("radio", { name: "Photo" }));
    await userEvent.click(await screen.findByRole("button", { name: /Sweater/ }));

    expect(await screen.findByText("Grey Jumper")).toBeInTheDocument();
    const upload = calls.find((c) => c.url.startsWith("/search/image"));
    expect(upload?.url).toBe("/search/image?top_k=9");
    expect(upload?.init?.method).toBe("POST");
    const file = (upload?.init?.body as FormData).get("file") as File;
    expect(file.name).toBe("714582004.jpg");
    expect(calls.some((c) => c.url === "/search/examples/714582004/image")).toBe(true);
  });

  it("refuses a file that is not a photo before uploading it", async () => {
    const calls = api({ "/sections": [], "/search/examples": [] });
    renderApp(<DiscoverPage />);
    await userEvent.click(screen.getByRole("radio", { name: "Photo" }));
    const input = screen.getByLabelText("Upload a photo of a garment");
    await userEvent.upload(input, new File(["%PDF"], "notes.pdf", { type: "application/pdf" }), {
      applyAccept: false,
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PNG or JPEG photo.");
    await waitFor(() => {
      expect(calls.some((c) => c.url.startsWith("/search/image"))).toBe(false);
    });
  });
});
