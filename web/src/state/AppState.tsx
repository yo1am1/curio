import { useState, type ReactNode } from "react";

import { type AppStores, createAppStores, StoresContext } from "./stores";

export function AppStateProvider({
  children,
  stores,
}: {
  children: ReactNode;
  stores?: AppStores;
}) {
  const [value] = useState(() => stores ?? createAppStores());
  return <StoresContext.Provider value={value}>{children}</StoresContext.Provider>;
}
