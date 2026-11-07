import { render } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";

import { Providers } from "../App";
import { DetailsProvider } from "../components/details/DetailsProvider";
import { type AppStores, createAppStores } from "../state/stores";

/** Render inside every app provider with fresh, isolated stores. */
export function renderApp(
  ui: ReactElement,
  stores: AppStores = createAppStores(localStorage, sessionStorage),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <Providers stores={stores} client={client}>
      <MemoryRouter>
        <DetailsProvider>{ui}</DetailsProvider>
      </MemoryRouter>
    </Providers>,
  );
  return { ...result, stores, client };
}
