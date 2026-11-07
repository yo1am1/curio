import { BookOpen, Moon, Shirt, Wine, type LucideProps } from "lucide-react";

import type { Domain } from "../../api/types";

const ICONS = { fashion: Shirt, cocktails: Wine, books: BookOpen, evening: Moon } as const;

export function DomainIcon({ domain, ...props }: { domain: Domain } & LucideProps) {
  const Icon = ICONS[domain];
  return <Icon aria-hidden="true" {...props} />;
}
