/// <reference types="vitest/config" />
import type { IncomingMessage } from "node:http";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

/**
 * The API keeps its paths at the root (no /api prefix), as in production, where FastAPI
 * serves this app itself. The dev server therefore proxies every request except page
 * navigations (GET with Accept: text/html), which Vite answers with index.html — the same
 * rule as the API's SPA fallback. Set CURIO_API_URL to point at another API.
 */
const OWN = /^\/(?:@|__vite|src\/|node_modules\/|favicon\.svg)/;

function isNavigation(req: IncomingMessage): boolean {
  return req.method === "GET" && (req.headers.accept ?? "").includes("text/html");
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.CURIO_API_URL || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      port: Number(env.CURIO_WEB_PORT || 5173),
      proxy: {
        "^/.*": {
          target,
          changeOrigin: true,
          bypass: (req) => (isNavigation(req) || OWN.test(req.url ?? "") ? req.url : undefined),
        },
      },
    },
    build: {
      target: "es2023",
      // No source maps in the build the API serves (and the image ships): they add bytes
      // and expose nothing users need. `vite` (the dev server) maps sources on its own.
      sourcemap: false,
      rolldownOptions: {
        output: {
          // Long-lived vendor chunks, so an app change does not re-download React.
          codeSplitting: {
            groups: [
              {
                name: "react",
                test: /node_modules[\\/](react|react-dom|react-router|scheduler|cookie)[\\/]/,
              },
              {
                name: "motion",
                test: /node_modules[\\/](motion|motion-dom|motion-utils|framer-motion)[\\/]/,
              },
              {
                name: "markdown",
                test: /node_modules[\\/](react-markdown|remark-.*|micromark.*|mdast-.*|hast-.*|unist-.*|unified|vfile.*|devlop|property-information|decode-named-character-reference|character-.*|ccount|markdown-table|trim-lines|space-separated-tokens|comma-separated-tokens|html-url-attributes|zwitch|longest-streak|bail|is-plain-obj|trough|estree-.*|style-to-.*|inline-style-parser|extend)[\\/]/,
              },
              { name: "vendor", test: /node_modules[\\/]/ },
            ],
          },
        },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      css: { modules: { classNameStrategy: "non-scoped" } },
    },
  };
});
