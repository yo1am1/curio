import { Wine } from "lucide-react";

import { useServed } from "../../api/queries";
import { MAX_SHORTLIST } from "../../domains/registry";
import { plural } from "../../domains/format";
import { useSession } from "../../state/stores";
import { Disclosure } from "../../ui/Disclosure";
import { PairingPanel } from "../cards/PairingPanel";
import styles from "./Books.module.css";

/** "Your reading list's cocktail": drinks for the reading list and the remembered liked
 * genres, when either is there and the server pairs books with drinks. */
export function ReadingListPairing() {
  const served = useServed();
  const books = useSession((s) => s.readingList);
  const liked = useSession((s) => s.bookPreferences.liked_genres);
  const pairable = !!served.get("books")?.enabled && !!served.get("cocktails")?.enabled;
  if (!pairable || !(books.length || liked.length)) return null;
  const ids = books.map((b) => b.book_id).slice(0, MAX_SHORTLIST);
  const basis = [
    books.length ? plural(books.length, "book") : null,
    liked.length ? `you like ${liked.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <section
      className={styles.pairing}
      aria-labelledby="reading-list-cocktail"
      data-testid="reading-list-pairing"
    >
      <Disclosure
        defaultOpen
        summary={
          <span className={styles.pairingHead}>
            <span className={styles.pairingIcon} aria-hidden="true">
              <Wine size={18} />
            </span>
            <span>
              <span id="reading-list-cocktail" className={styles.pairingTitle}>
                Your reading list&apos;s cocktail
              </span>
              <span className={styles.pairingBasis}>Paired with {basis}</span>
            </span>
          </span>
        }
      >
        <PairingPanel request={{ book_ids: ids, liked_genres: liked }} compact />
      </Disclosure>
    </section>
  );
}
