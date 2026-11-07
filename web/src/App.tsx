import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router";

import { useServed } from "./api/queries";
import type { Domain } from "./api/types";
import { BecausePage } from "./components/books/BecausePage";
import { BookSearchPage } from "./components/books/BookSearchPage";
import { MoodsPage } from "./components/books/MoodsPage";
import { CocktailSearchPage } from "./components/cocktails/CocktailSearchPage";
import { PantryPage } from "./components/cocktails/PantryPage";
import { ConnectionsPage } from "./components/fashion/ConnectionsPage";
import { DiscoverPage } from "./components/fashion/DiscoverPage";
import { ForYouPage } from "./components/fashion/ForYouPage";
import { QualityPage } from "./components/fashion/QualityPage";
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

/** A domain's pages, when the server serves the domain; else the landing. */
function DomainRoute({ domain }: { domain: Domain }) {
  const served = useServed();
  if (!served.get(domain)?.enabled) return <Navigate to="/" replace />;
  return <Outlet />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Landing />} />
        <Route path="evening" element={<DomainRoute domain="evening" />}>
          <Route index element={<EveningPage />} />
        </Route>
        {/* Page paths never sit under an API path (see DOMAIN_PAGES). */}
        <Route path="fashion" element={<DomainRoute domain="fashion" />}>
          <Route index element={<ForYouPage />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="connections" element={<ConnectionsPage />} />
          <Route path="quality" element={<QualityPage />} />
        </Route>
        <Route path="drinks" element={<DomainRoute domain="cocktails" />}>
          <Route index element={<CocktailSearchPage />} />
          <Route path="pantry" element={<PantryPage />} />
        </Route>
        <Route path="reading" element={<DomainRoute domain="books" />}>
          <Route index element={<BookSearchPage />} />
          <Route path="because" element={<BecausePage />} />
          <Route path="moods" element={<MoodsPage />} />
        </Route>
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
