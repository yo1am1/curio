import { useMemo } from "react";

import { useBecause, useBookIndex } from "../../api/queries";
import { LIMITS, becauseBody } from "../../domains/requests";
import { isNumberList, usePageState } from "../../state/pageState";
import { useSession, useStores } from "../../state/stores";
import { Combobox } from "../../ui/Combobox";
import { BookCard, ItemGrid } from "../cards/ItemCards";
import browse from "../browse/Browse.module.css";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import { useBooksIntro } from "./useBooksIntro";
import { ReadingListPairing } from "./ReadingListPairing";
import { ReadingTastesNote } from "./ReadingTastesNote";

/** "Because you liked…": books you loved, and what readers who loved them also loved. */
export function BecausePage() {
  const index = useBookIndex();
  const memory = useSession((s) => s.bookPreferences);
  const stores = useStores();
  const intro = useBooksIntro();
  // A first visit starts from the reading list.
  const [loved, setLoved] = usePageState<number[]>(
    "reading.loved",
    stores.session
      .get()
      .readingList.map((b) => b.book_id)
      .slice(0, LIMITS.loved),
    isNumberList,
  );
  const titles = useMemo(() => {
    const map = new Map<number, string>();
    for (const ref of index.data ?? []) map.set(ref.book_id, `${ref.title} · ${ref.author}`);
    for (const book of stores.session.get().readingList) {
      if (!map.has(book.book_id)) map.set(book.book_id, book.title);
    }
    return map;
  }, [index.data, stores.session]);
  const options = useMemo(() => (index.data ?? []).map((ref) => ref.book_id), [index.data]);
  const body = becauseBody(loved, memory);
  const results = useBecause(body);

  return (
    <div className={browse.page}>
      <PageHeader title="Books" documentTitle="Because you liked… · Books" intro={intro} />
      <ReadingListPairing />
      <section className={browse.panel} aria-label="Because you liked…">
        <div className={browse.panelHead}>
          <h2 className={browse.panelTitle}>Because you liked…</h2>
          <p className={browse.panelIntro}>
            Pick books you loved. Curio finds what readers who loved them also loved, from about 6
            million real ratings.
          </p>
        </div>
        <Combobox<number>
          label="Books you loved"
          options={options}
          value={loved}
          onChange={setLoved}
          optionLabel={(id) => titles.get(id) ?? `Book ${String(id)}`}
          max={LIMITS.loved}
          placeholder={
            index.isPending ? "Loading 10,000 titles…" : "Start typing a title, e.g. The Hobbit"
          }
        />
        <ReadingTastesNote memory={memory} boost={false} />
      </section>
      <Results
        title="Readers like you also loved"
        query={results}
        enabled={body !== null}
        kind="book"
        noun="book"
        idle="Add at least one book you loved, or save books to your reading list first."
        empty="No shared readers yet. Try another book you loved."
      >
        {(hits) => (
          <ItemGrid label="Readers like you also loved" kind="book">
            {hits.map((hit) => (
              <BookCard key={hit.book.book_id} book={hit.book} reason={hit.reasons?.[0]} />
            ))}
          </ItemGrid>
        )}
      </Results>
    </div>
  );
}
