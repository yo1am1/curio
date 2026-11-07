import { useBookGenres, useBookMoods, useBookSearch } from "../../api/queries";
import { browseBody } from "../../domains/requests";
import { isStringOrNull, usePageState } from "../../state/pageState";
import { useSession } from "../../state/stores";
import { Field, Select } from "../../ui/Field";
import { Segmented } from "../../ui/Segmented";
import { Shimmer } from "../../ui/Skeleton";
import { BookCard, ItemGrid } from "../cards/ItemCards";
import browse from "../browse/Browse.module.css";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import { useBooksIntro } from "./useBooksIntro";
import styles from "./Books.module.css";
import { ReadingListPairing } from "./ReadingListPairing";
import { ReadingTastesNote } from "./ReadingTastesNote";

/** Browse by mood: the best-loved books in a mood, a genre, or both. */
export function MoodsPage() {
  const genres = useBookGenres();
  const moods = useBookMoods();
  const memory = useSession((s) => s.bookPreferences);
  const intro = useBooksIntro();
  const [mood, setMood] = usePageState<string | null>("reading.browse-mood", null, isStringOrNull);
  const [genre, setGenre] = usePageState<string | null>(
    "reading.browse-genre",
    null,
    isStringOrNull,
  );
  const body = browseBody(mood, genre, memory);
  const results = useBookSearch(body);

  return (
    <div className={browse.page}>
      <PageHeader title="Books" documentTitle="Browse by mood · Books" intro={intro} />
      <ReadingListPairing />
      <section className={browse.panel} aria-label="Browse by mood">
        <div className={browse.panelHead}>
          <h2 className={browse.panelTitle}>Browse by mood</h2>
        </div>
        {moods.isPending ? (
          <Shimmer height="2.6rem" />
        ) : (
          <Segmented
            label="Mood"
            options={(moods.data ?? []).map((name) => ({ value: name, label: name }))}
            value={mood}
            allowNone
            wrap
            onChange={(value) => {
              setMood(mood === value ? null : value);
            }}
          />
        )}
        <div className={styles.genre}>
          <Field label="Genre">
            {({ id }) => (
              <Select
                id={id}
                value={genre ?? ""}
                onChange={(event) => {
                  setGenre(event.target.value || null);
                }}
              >
                <option value="">Any genre</option>
                {(genres.data ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <ReadingTastesNote memory={memory} />
      </section>
      <Results
        title={[mood, genre].filter(Boolean).join(" · ") || "Books"}
        query={results}
        enabled={body !== null}
        kind="book"
        noun="book"
        idle="Pick a mood or a genre to browse the best-loved books in it."
        empty="Nothing here yet. Try another mood or genre."
      >
        {(hits) => (
          <ItemGrid label="Books in this mood" kind="book">
            {hits.map((hit) => (
              <BookCard key={hit.book.book_id} book={hit.book} reason={hit.reasons?.[0]} />
            ))}
          </ItemGrid>
        )}
      </Results>
    </div>
  );
}
