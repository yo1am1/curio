import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { useServed } from "./api/queries";
import { ChatRoute } from "./components/chat/ChatPage";
import { DetailsProvider } from "./components/details/DetailsProvider";
import { EveningPage } from "./components/evening/EveningPage";
import { AppShell } from "./components/shell/AppShell";
import { NotFound } from "./components/shell/NotFound";
import { DEFAULT_DOMAIN, DOMAIN_ORDER, landingPath } from "./domains/registry";
import { AppStateProvider } from "./state/AppState";
import type { AppStores } from "./state/stores";
import { ToastProvider } from "./ui/Toasts";

/** A new visit opens on Plan my evening when the server serves the evening, else on the
 * first served domain. */
function Landing() {
  const served = useServed();
  const first =
    served.get(DEFAULT_DOMAIN)?.enabled === true
      ? DEFAULT_DOMAIN
      : DOMAIN_ORDER.find((domain) => served.get(domain)?.enabled);
  if (!first) return null;
  return <Navigate to={landingPath(first)} replace />;
}

function EveningRoute() {
  const served = useServed();
  if (!served.get("evening")?.enabled) return <Navigate to="/" replace />;
  return <EveningPage />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Landing />} />
        <Route path="evening" element={<EveningRoute />} />
        <Route path="ask/:domain" element={<ChatRoute />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function Providers({
  children,
  stores,
  client,
}: {
  children: ReactNode;
  stores?: AppStores;
  client?: QueryClient;
}) {
  const [queryClient] = useState(
    () =>
      client ??
      new QueryClient({
        defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
      }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <AppStateProvider stores={stores}>
        <MotionConfig reducedMotion="user">
          <ToastProvider>{children}</ToastProvider>
        </MotionConfig>
      </AppStateProvider>
    </QueryClientProvider>
  );
}

export default function App() {
  return (
    <Providers>
      <BrowserRouter>
        <DetailsProvider>
          <AppRoutes />
        </DetailsProvider>
      </BrowserRouter>
    </Providers>
  );
}
