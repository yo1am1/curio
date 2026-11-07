import { Search } from "lucide-react";

import { useBookGenres, useBookMoods, useBookSearch } from "../../api/queries";
import { LIMITS, bookSearchBody } from "../../domains/requests";
import { isNumberOrNull, isStringList, isStringOrNull, usePageState } from "../../state/pageState";
import { useSession } from "../../state/stores";
import { Button } from "../../ui/Button";
import { Combobox } from "../../ui/Combobox";
import { Field, Select, TextInput } from "../../ui/Field";
import { BookCard, ItemGrid } from "../cards/ItemCards";
import browse from "../browse/Browse.module.css";
import { Examples } from "../browse/Examples";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import styles from "./Books.module.css";
import { ReadingListPairing } from "./ReadingListPairing";
import { ReadingTastesNote } from "./ReadingTastesNote";
import { useBooksIntro } from "./useBooksIntro";

const EXAMPLES = ["Fantasy like The Name of the Wind", "Short classic novels", "A cosy mystery"];
const LENGTHS: { value: number | null; label: string }[] = [
  { value: null, label: "Any length" },
  { value: 200, label: "Up to 200 pages" },
  { value: 300, label: "Up to 300" },
  { value: 400, label: "Up to 400" },
];

/** Book search: a description, genres, a mood and a length. */
export function BookSearchPage() {
  const genres = useBookGenres();
  const moods = useBookMoods();
  const memory = useSession((s) => s.bookPreferences);
  const intro = useBooksIntro();
  const [draft, setDraft] = usePageState("reading.draft", "");
  const [query, setQuery] = usePageState("reading.query", "");
  const [chosen, setChosen] = usePageState<string[]>("reading.genres", [], isStringList);
  const [mood, setMood] = usePageState<string | null>("reading.mood", null, isStringOrNull);
  const [pages, setPages] = usePageState<number | null>("reading.pages", null, isNumberOrNull);
  const body = bookSearchBody({ query, genres: chosen, mood, maxPages: pages }, memory);
  const results = useBookSearch(body);

  return (
    <div className={browse.page}>
      <PageHeader title="Books" documentTitle="Search · Books" intro={intro} />
      <ReadingListPairing />
      <form
        className={browse.panel}
        aria-label="Find a book"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft);
        }}
      >
        <div className={browse.panelHead}>
          <h2 className={browse.panelTitle}>Find a book</h2>
        </div>
        <div className={browse.searchRow}>
          <Field label="Describe a book">
            {({ id }) => (
              <TextInput
                id={id}
                search
                value={draft}
                maxLength={200}
                placeholder="e.g. fantasy like The Name of the Wind"
                autoComplete="off"
                enterKeyHint="search"
                onChange={(event) => {
                  setDraft(event.target.value);
                }}
              />
            )}
          </Field>
          <Button type="submit" variant="primary" icon={<Search />} className={styles.submit}>
            Find books
          </Button>
        </div>
        <div className={styles.filters}>
          <Combobox
            label="Genres"
            options={genres.data ?? []}
            value={chosen}
            onChange={setChosen}
            max={LIMITS.genres}
            placeholder={genres.isPending ? "Loading genres…" : "Any genre"}
          />
          <Field label="Mood">
            {({ id }) => (
              <Select
                id={id}
                value={mood ?? ""}
                onChange={(event) => {
                  setMood(event.target.value || null);
                }}
              >
                <option value="">Any mood</option>
                {(moods.data ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Length">
            {({ id }) => (
              <Select
                id={id}
                value={pages ?? ""}
                onChange={(event) => {
                  setPages(event.target.value ? Number(event.target.value) : null);
                }}
              >
                {LENGTHS.map((length) => (
                  <option key={length.label} value={length.value ?? ""}>
                    {length.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <ReadingTastesNote memory={memory} />
        <Examples
          examples={EXAMPLES}
          current={query}
          onPick={(example) => {
            setDraft(example);
            setQuery(example);
          }}
        />
      </form>
      <Results
        title="Matching books"
        query={results}
        enabled={body !== null}
        kind="book"
        noun="book"
        idle="Describe a book, pick a genre or a mood, or try an example."
        empty="No matching books. Try fewer filters or another description."
      >
        {(hits) => (
          <ItemGrid label="Matching books" kind="book">
            {hits.map((hit) => (
              <BookCard key={hit.book.book_id} book={hit.book} reason={hit.reasons?.[0]} />
            ))}
          </ItemGrid>
        )}
      </Results>
    </div>
  );
}
