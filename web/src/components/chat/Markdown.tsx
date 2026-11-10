import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./Markdown.module.css";

/** The sites the catalogs come from. A link in model text opens only on these: a link to any
 * other host could carry whatever the model was talked into putting in its address. */
const LINK_HOSTS = new Set([
  "www.thecocktaildb.com",
  "thecocktaildb.com",
  "www.goodreads.com",
  "goodreads.com",
  "openlibrary.org",
  "www2.hm.com",
]);

/** Links in model text open only `https:` addresses on the catalogs' own sites. Anything
 * else (`javascript:`, `data:`, `http:`, relative or protocol-relative, another host, an
 * address with a user name or a port) is shown as plain text, not a link. */
function safeHref(url: string): string {
  const text = url.trim();
  if (!/^https:\/\/[^/\s]/i.test(text)) return "";
  let parsed: URL;
  try {
    parsed = new URL(text);
  } catch {
    return "";
  }
  if (parsed.username || parsed.password || parsed.port) return "";
  return LINK_HOSTS.has(parsed.hostname.toLowerCase()) ? text : "";
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

/** Model text as markdown; raw HTML and images are never rendered, links only to the
 * catalogs' sites over https. */
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
