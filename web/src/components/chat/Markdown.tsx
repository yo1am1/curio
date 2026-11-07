import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./Markdown.module.css";

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
};

/** Images in model text are dropped, never fetched: `![](https://host/?q=secret)` would
 * otherwise make the browser send whatever the model was talked into putting in the URL.
 * Cards show catalog photos; chat text needs none. */
const DISALLOWED = ["img"];

/** Model text as markdown; raw HTML and images are never rendered. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
        disallowedElements={DISALLOWED}
        skipHtml
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
