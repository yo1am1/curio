import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Wine } from "lucide-react";
import { useState } from "react";

import { postJson } from "../../api/client";
import type { BookPairing, PairingRequest } from "../../api/types";
import { useSession } from "../../state/stores";
import { Button } from "../../ui/Button";
import { ErrorMessage } from "../../ui/StateMessage";
import { Shimmer } from "../../ui/Skeleton";
import { Toggle } from "../../ui/Toggle";
import { pairingTastes } from "./pairing";
import { PairingCard } from "./PairingCard";
import styles from "./PairingCard.module.css";

/** Drinks for a book or a reading list, with an alcohol-free toggle and "Another pairing",
 * which cycles through the pairer's sets. */
export function PairingPanel({
  request,
  title,
  compact = false,
}: {
  request: Pick<PairingRequest, "book_id" | "book_ids" | "liked_genres">;
  title?: string;
  compact?: boolean;
}) {
  const [alcoholFree, setAlcoholFree] = useState(false);
  const [seed, setSeed] = useState(0);
  const bookPreferences = useSession((s) => s.bookPreferences);
  const cocktailPreferences = useSession((s) => s.cocktailPreferences);
  const body = {
    ...request,
    ...pairingTastes({ bookPreferences, cocktailPreferences }),
    alcohol_free: alcoholFree,
    seed,
  } as PairingRequest;
  const { data, error, isPending, isFetching, refetch } = useQuery({
    queryKey: ["pairing", body],
    queryFn: ({ signal }) => postJson<BookPairing>("/pairings/book-cocktail", body, signal),
    staleTime: 5 * 60_000,
    placeholderData: (previous) => previous,
    retry: false,
  });
  const sets = data?.sets ?? 1;
  return (
    <div className={styles.panel} aria-busy={isFetching}>
      <div className={styles.panelBar}>
        {title ? (
          <h3 className={styles.panelTitle}>
            <Wine size={16} aria-hidden="true" />
            {title}
          </h3>
        ) : null}
        <Toggle
          checked={alcoholFree}
          onChange={(value) => {
            setAlcoholFree(value);
            setSeed(0);
          }}
        >
          Alcohol-free only
        </Toggle>
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw />}
          disabled={sets < 2 || isFetching}
          title="Another set of drinks for the same profile"
          onClick={() => {
            setSeed((seed + 1) % Math.max(sets, 1));
          }}
        >
          Another pairing
        </Button>
      </div>
      {isPending ? <Shimmer height="12rem" /> : null}
      {error && !data ? (
        <ErrorMessage
          error={error}
          retry={() => {
            void refetch();
          }}
        />
      ) : null}
      {data ? <PairingCard result={data} compact={compact} /> : null}
    </div>
  );
}
