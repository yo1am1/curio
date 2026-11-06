"""Book pages: search, "Because you liked", browse, the book dialog, the book → drink
pairing, and the book chat cards and session panel."""

import re
import time
from collections.abc import Callable

import httpx
import streamlit as st

from curio.app.ui.client import call_api
from curio.app.ui.cocktails import (
    OPEN_DRINK,
    add_cocktail,
    alcohol_badge,
    cocktail_details,
    cocktail_image,
    cocktail_memory,
    cocktail_shortlist,
)
from curio.app.ui.common import (
    BOOK_COPY,
    BOOK_CREDIT,
    BOOK_RATINGS,
    BOOK_SOURCE,
    BOOKS,
    COCKTAILS,
    COVER_CREDIT,
    MAX_SHORTLIST,
    MAX_TASTES,
    approval_toggles,
    merge_newest,
    plain,
    queue_message,
    remember_input,
    restore_input,
    served_domains,
)
from curio.domains.books.catalog import OPEN_LIBRARY_BOOK, open_library_cover_url

READING_LIST_FULL = f"Your reading list holds up to {MAX_SHORTLIST} books."


def reading_list() -> list[dict]:
    return st.session_state.setdefault("book_shortlist", [])


def add_book(book: dict, announce: bool = True) -> bool:
    """Add a book unless it is listed already or the list is full; return whether it was added."""
    items = reading_list()
    if any(item["book_id"] == book["book_id"] for item in items):
        return False
    if len(items) >= MAX_SHORTLIST:
        if announce:
            st.toast(
                f"Your reading list holds up to {MAX_SHORTLIST} books.", icon=":material/info:"
            )
        return False
    items.append(book)
    if announce:
        st.toast(f"Added {book['title']} to your reading list.", icon=":material/bookmark:")
    return True


def remove_book(book_id: int) -> None:
    st.session_state["book_shortlist"] = [b for b in reading_list() if b["book_id"] != book_id]


def reading_memory() -> dict:
    return st.session_state.setdefault("book_preferences", {})


def forget_reading_taste(field: str, value: object) -> None:
    memory = reading_memory()
    memory[field] = [item for item in memory.get(field, []) if item != value]


def remember_reading_tastes(tastes: dict) -> list:
    """Store approved reading tastes; return older values dropped to stay within the limits."""
    memory = reading_memory()
    dropped = []
    for field in ("liked_genres", "avoided_genres", "avoided_authors"):
        memory[field], lost = merge_newest(memory.get(field, []), tastes.get(field, []), MAX_TASTES)
        dropped += lost
    return dropped


def show_chat_books(books: list[dict], index: int, context: str) -> None:
    saved = {item["book_id"] for item in reading_list()}
    compact = context == "popup"
    columns = None if compact else st.columns(min(5, max(3, len(books))))
    for position, book in enumerate(books):
        bid = book["book_id"]
        slot = st.container() if compact else columns[position % len(columns)]
        with (
            slot,
            st.container(
                key=f"chat_book_{context}_{index}_{bid}",
                horizontal=compact,
                vertical_alignment="center" if compact else "top",
            ),
        ):
            book_cover(
                book,
                width=56 if compact else "stretch",
                key="" if compact else f"chat_{context}_{index}_{bid}",
            )
            with st.container(gap=None):
                st.text(book["title"])
                st.caption(f"{book['authors'][0]} · ★ {book['average_rating']:.2f}")
            with st.container(horizontal=True, gap="small"):
                if st.button("View", key=f"chat_book_view_{context}_{index}_{bid}"):
                    book_details(bid)
                st.button(
                    "Saved" if bid in saved else "Save",
                    key=f"chat_book_save_{context}_{index}_{bid}",
                    icon=":material/bookmark:" if bid in saved else ":material/bookmark_border:",
                    type="tertiary",
                    disabled=bid in saved,
                    on_click=add_book,
                    args=(book,),
                )


def book_context() -> dict:
    context: dict = {"domain": BOOKS}
    memory = {key: value for key, value in reading_memory().items() if value}
    if memory:
        context["book_preferences"] = memory
    if reading_list():
        context["book_shortlist"] = [b["book_id"] for b in reading_list()]
    # Drink pairings honour the remembered cocktail tastes (alcohol-free, avoided ingredients).
    if tastes := {key: value for key, value in cocktail_memory().items() if value}:
        context["cocktail_preferences"] = tastes
    return context


def reading_taste_chips(memory: dict) -> list[tuple[str, object, str]]:
    """Remembered reading tastes as (field, value, label) rows."""
    return [
        *(("liked_genres", v, f"Likes {v}") for v in memory.get("liked_genres", [])),
        *(("avoided_genres", v, f"No {v}") for v in memory.get("avoided_genres", [])),
        *(("avoided_authors", v, f"No {v}") for v in memory.get("avoided_authors", [])),
    ]


def book_session_panel() -> None:
    copy = BOOK_COPY
    with st.container(key="agent_session", border=True):
        st.subheader("Your session", anchor=False)
        approval_toggles()

        st.markdown("**Memory**")
        chips = reading_taste_chips(reading_memory())
        if not chips:
            st.caption(copy.memory_hint)
        with st.container(horizontal=True, key="session_memory"):
            for field, value, label in chips:
                st.button(
                    label,
                    key=f"forget_book_{field}_{value}",
                    icon=":material/close:",
                    help="Forget this taste",
                    on_click=forget_reading_taste,
                    args=(field, value),
                )

        items = reading_list()
        st.markdown(f"**Reading list · {len(items)}**")
        if not items:
            st.caption(copy.shortlist_hint)
        for book in items:
            with st.container(
                key=f"reading_list_{book['book_id']}",
                horizontal=True,
                vertical_alignment="center",
            ):
                book_cover(book, width=40)
                with st.container(gap=None, width="stretch"):
                    st.text(book["title"])
                    st.caption(book["authors"][0])
                st.button(
                    "Remove",
                    icon=":material/close:",
                    key=f"reading_list_remove_{book['book_id']}",
                    help=f"Remove {book['title']}",
                    type="tertiary",
                    on_click=remove_book,
                    args=(book["book_id"],),
                )
        if len(items) >= 2:
            st.button(
                "Ask Curio which to read first",
                icon=":material/chat_bubble:",
                key="reading_list_ask",
                on_click=queue_message,
                args=(copy.compare_prompt,),
            )


BOOK_EXAMPLES = [
    "Fantasy like The Name of the Wind",
    "Short classic novels",
    "A cosy mystery",
]
BOOK_LENGTHS = {None: "Any length", 200: "Up to 200 pages", 300: "Up to 300", 400: "Up to 400"}
BOOK_TOP_K = 12
BOOK_COLUMNS = 4
_MEDIUM_COVER = re.compile(r"(/books/\d+)m/")
BOOKMARK = {True: ":material/bookmark:", False: ":material/bookmark_border:"}


@st.cache_data(ttl=3600, show_spinner=False)
def book_index() -> list[dict]:
    """Every book's id, title and author (most rated first), for the "books you loved" picker."""
    return call_api("GET", "/books/index") or []


# Open Library answers a missing cover with a 404 ("default=false"); the browser would show a
# broken image, so the UI asks first. Answers are cached; a network failure is not, and
# pauses the lookups for a minute so a page never waits on every cover.
COVER_TIMEOUT = 3.0
COVER_OUTAGE_SECONDS = 60.0
_cover_outage_until = 0.0


@st.cache_data(ttl=7 * 24 * 3600, max_entries=5000, show_spinner=False)
def _open_library_cover(url: str) -> bool:
    """Whether Open Library has the cover at `url`; raises (uncached) when it cannot say."""
    response = httpx.head(url, timeout=COVER_TIMEOUT, follow_redirects=True)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return response.headers.get("content-type", "").startswith("image/")


def open_library_cover(book: dict) -> str | None:
    """The Open Library cover for a book without a Goodreads one, when Open Library has it.

    The URL is built from the book's checked ISBN-13, never taken from the API.
    """
    global _cover_outage_until
    url = open_library_cover_url(book.get("isbn"))
    if url is None or time.monotonic() < _cover_outage_until:
        return None
    try:
        return url if _open_library_cover(url) else None
    except httpx.HTTPError:
        _cover_outage_until = time.monotonic() + COVER_OUTAGE_SECONDS
        return None


def book_cover(book: dict, width: int | str = "stretch", *, key: str = "") -> None:
    """The browser loads covers straight from Goodreads, or from Open Library by ISBN when
    Goodreads has none; only https is shown.

    Full-width slots without a cover get a `missing_photo_<key>` placeholder with the title.
    """
    url = book.get("image_url")
    if url and url.startswith("https://"):
        if width == "stretch":  # the dataset links 98 px covers; Goodreads serves 318 px too
            url = _MEDIUM_COVER.sub(r"\1l/", url)
        # The caption names the cover for screen readers; ui.css hides it visually.
        st.image(url, caption=book["title"], width=width)
    elif cover := open_library_cover(book):
        st.image(cover, caption=book["title"], width=width)
        if key.startswith("detail_"):
            st.caption(f"Cover: [Open Library]({OPEN_LIBRARY_BOOK.format(book['isbn'])})")
    elif key:
        with st.container(key=f"missing_photo_{key}"):
            st.caption(plain(book["title"]), text_alignment="center")


def primary_author(book: dict) -> str:
    """The book's author for a card or dialog: the first listed, since Goodreads lists
    illustrators and translators after the author (Harry Potter's "Mary GrandPré")."""
    return book["authors"][0]


def rating_line(book: dict) -> str:
    return f"{book['average_rating']:.2f} · {book['ratings_count']:,} Goodreads ratings"


def book_facts(book: dict) -> str:
    """Year, length and series, as a caption."""
    facts = []
    if (year := book.get("year")) is not None:
        facts.append(str(year) if year > 0 else f"{-year} BC")
    if book.get("pages"):
        facts.append(f"{book['pages']} pages")
    if book.get("series"):
        position = f" #{book['series_position']}" if book.get("series_position") else ""
        facts.append(f"{book['series']}{position}")
    return " · ".join(plain(fact) for fact in facts)


def show_books(
    books: list[dict],
    *,
    context: str,
    annotate: Callable[[int], None] | None = None,
) -> None:
    saved = {item["book_id"] for item in reading_list()}
    for start in range(0, len(books), BOOK_COLUMNS):
        columns = st.columns(BOOK_COLUMNS)
        for offset, book in enumerate(books[start : start + BOOK_COLUMNS]):
            i = start + offset
            bid = book["book_id"]
            with (
                columns[offset],
                st.container(key=f"book_card_{context}_{bid}_{i}", height="stretch"),
            ):
                book_cover(book, key=f"{context}_book_{bid}_{i}")
                with st.container(key=f"book_info_{context}_{bid}_{i}", gap=None):
                    st.text(book["title"])
                    st.caption(plain(primary_author(book)))
                with st.container(
                    horizontal=True,
                    vertical_alignment="center",
                    horizontal_alignment="distribute",
                ):
                    st.badge(
                        f"★ {book['average_rating']:.2f}",
                        color="violet",
                        help=f"Goodreads average of {book['ratings_count']:,} ratings",
                    )
                    st.button(
                        "Saved" if bid in saved else "Save",
                        key=f"book_save_{context}_{bid}_{i}",
                        icon=":material/bookmark:"
                        if bid in saved
                        else ":material/bookmark_border:",
                        type="tertiary",
                        disabled=bid in saved,
                        on_click=add_book,
                        args=(book,),
                    )
                if annotate is not None:
                    annotate(i)
                st.space("stretch")
                if st.button(
                    f"View {book['title']}", key=f"book_view_{context}_{bid}_{i}", width="stretch"
                ):
                    book_details(bid)


def show_book_hits(hits: list[dict] | None, *, context: str, empty: str) -> None:
    if not hits:
        if hits is not None:
            st.info(empty)
        return

    def reason(i: int) -> None:
        if hits[i].get("reasons"):
            st.caption(plain(hits[i]["reasons"][0]))

    show_books([hit["book"] for hit in hits], context=context, annotate=reason)


def use_book_example(query: str) -> None:
    st.session_state["book_query"] = query


def avoided() -> dict:
    """Remembered avoidances as search filters, so the pages respect them like the agent."""
    memory = reading_memory()
    return {
        "exclude_genres": memory.get("avoided_genres", []),
        "exclude_authors": memory.get("avoided_authors", []),
    }


def book_search_panel(genres: list[str], moods: list[str]) -> None:
    with st.container(key="book_search_panel", border=True):
        st.subheader("Find a book")
        with st.form("book_search", border=False):
            restore_input("book_query")
            query = st.text_input(
                "Describe a book",
                key="book_query",
                placeholder="e.g. fantasy like The Name of the Wind",
                max_chars=200,
            )
            remember_input("book_query", query)
            left, middle, right = st.columns([0.42, 0.3, 0.28], vertical_alignment="bottom")
            with left:
                restore_input("book_genres")
                chosen = st.multiselect(
                    "Genres",
                    genres,
                    key="book_genres",
                    max_selections=3,
                    placeholder="Any genre",
                )
                remember_input("book_genres", chosen)
            with middle:
                mood = st.selectbox(
                    "Mood", [None, *moods], format_func=lambda m: m or "Any mood", key="book_mood"
                )
            with right:
                pages = st.selectbox(
                    "Length", list(BOOK_LENGTHS), format_func=BOOK_LENGTHS.get, key="book_pages"
                )
            st.form_submit_button(
                "Find books", type="primary", key="book_search_submit", width="stretch"
            )
            st.caption("Or try an example")
            with st.container(key="book_example", horizontal=True):
                for index, example in enumerate(BOOK_EXAMPLES):
                    st.form_submit_button(
                        example,
                        key=f"book_example_{index}",
                        on_click=use_book_example,
                        args=(example,),
                    )
    if not (query.strip() or chosen or mood):
        return
    body = {
        "query": query.strip(),
        "genres": chosen,
        "mood": mood,
        "max_pages": pages,
        "boost_genres": reading_memory().get("liked_genres", []),
        "top_k": BOOK_TOP_K,
        **avoided(),
    }
    with st.container(key="book_search_results"):
        st.subheader("Matching books")
        with st.spinner("Searching books…"):
            show_book_hits(
                call_api("POST", "/books/search", json=body),
                context="search",
                empty="No matching books. Try fewer filters or another description.",
            )


def because_panel() -> None:
    index = book_index()
    titles = {b["book_id"]: f"{b['title']} · {b['author']}" for b in index}
    with st.container(key="because_panel", border=True):
        st.subheader("Because you liked…")
        st.caption(
            "Pick books you loved. Curio finds what readers who loved them also loved, "
            "from about 6 million real ratings."
        )
        if "book_loved" not in st.session_state and "saved_book_loved" not in st.session_state:
            # First visit: start from the reading list.
            st.session_state["book_loved"] = [b["book_id"] for b in reading_list()][:10]
        restore_input("book_loved")
        loved = st.multiselect(
            "Books you loved",
            list(titles),
            format_func=lambda bid: titles.get(bid, f"Book {bid}"),
            key="book_loved",
            max_selections=10,
            placeholder="Start typing a title, e.g. The Hobbit",
        )
        remember_input("book_loved", loved)
    if not loved:
        st.caption("Add at least one book you loved, or save books to your reading list first.")
        return
    body = {"liked": loved, "top_k": BOOK_TOP_K, **avoided()}
    with st.container(key="because_results"):
        st.subheader("Readers like you also loved")
        with st.spinner("Asking fellow readers…"):
            show_book_hits(
                call_api("POST", "/books/because", json=body),
                context="because",
                empty="No shared readers yet. Try another book you loved.",
            )


def browse_panel(genres: list[str], moods: list[str]) -> None:
    with st.container(key="browse_panel", border=True):
        st.subheader("Browse by mood")
        mood = st.pills("Mood", moods, key="browse_mood", label_visibility="collapsed")
        genre = st.selectbox(
            "Genre", [None, *genres], format_func=lambda g: g or "Any genre", key="browse_genre"
        )
    if not (mood or genre):
        st.caption("Pick a mood or a genre to browse the best-loved books in it.")
        return
    body = {
        "mood": mood,
        "genres": [genre] if genre else [],
        "boost_genres": reading_memory().get("liked_genres", []),
        "top_k": BOOK_TOP_K,
        **avoided(),
    }
    with st.container(key="browse_results"):
        st.subheader(" · ".join(part for part in (mood, genre) if part))
        show_book_hits(
            call_api("POST", "/books/search", json=body),
            context="browse",
            empty="Nothing here yet. Try another mood or genre.",
        )


def books_page() -> None:
    st.title("Books")
    count = st.session_state.get("service_health", {}).get("book_count")
    st.caption(
        f"Search {count:,} popular books, start from one you loved, or browse by mood."
        if count
        else "Search popular books, start from one you loved, or browse by mood."
    )
    reading_list_pairing()
    mode = st.segmented_control(
        "Find books by",
        ["search", "because", "browse"],
        format_func={
            "search": "Search",
            "because": "Because you liked…",
            "browse": "Browse by mood",
        }.get,
        default="search",
        required=True,
        key="book_mode",
        label_visibility="collapsed",
    )
    if mode == "because":
        because_panel()
        return
    genres = call_api("GET", "/books/genres")
    moods = call_api("GET", "/books/moods")
    if genres is None or moods is None:
        return
    (browse_panel if mode == "browse" else book_search_panel)(genres, moods)


@st.dialog("Book", width="large")
def book_details(book_id: int) -> None:
    book = call_api("GET", f"/books/{book_id}")
    if book is None:
        return
    saved = any(b["book_id"] == book_id for b in reading_list())
    with st.container(key="book_detail_body"):
        left, right = st.columns([0.32, 0.68], gap="large")
        with left:
            book_cover(book, key=f"detail_book_{book_id}")
        with right:
            st.subheader(book["title"], anchor=False)
            st.markdown(f"by {plain(primary_author(book))}")
            st.badge(rating_line(book), color="violet", icon=":material/star:")
            if facts := book_facts(book):
                st.caption(facts)
            if book.get("genres"):
                st.markdown(" ".join(f":blue-badge[{plain(g)}]" for g in book["genres"]))
            if book.get("description"):
                st.text(book["description"])
            if book.get("tags"):
                st.caption("Readers shelve it as " + plain(", ".join(book["tags"][:8])))
            with st.container(horizontal=True):
                st.button(
                    "On your reading list" if saved else "Save to reading list",
                    icon=":material/bookmark:" if saved else ":material/bookmark_border:",
                    key=f"book_detail_save_{book_id}",
                    disabled=saved,
                    on_click=add_book,
                    args=(book,),
                )
                if st.button(
                    "Ask Curio about it",
                    icon=":material/chat_bubble:",
                    key=f"book_detail_ask_{book_id}",
                ):
                    # Dialogs rerun on their own; a full rerun closes this one and opens chat.
                    queue_message(
                        f"Tell me about {book['title']} (book {book_id}).", True, domain=BOOKS
                    )
                    st.rerun()
                st.link_button("Goodreads", book["source_url"], icon=":material/open_in_new:")
                if pairing_available():
                    st.button(
                        "Pair a drink",
                        icon=":material/local_bar:",
                        key=f"book_detail_pair_{book_id}",
                        on_click=toggle_pairing,
                        args=(book_id,),
                    )
    if pairing_available() and st.session_state.get(f"{PAIR_OPEN}{book_id}"):
        with st.container(key="book_pairing", border=True):
            st.markdown(":material/local_bar: **Pour me something to read with**")
            pairing_panel({"book_id": book_id}, key=f"book_{book_id}", in_dialog=True)
    similar, by_author = st.columns(2, gap="large")
    # Buttons cannot open a second native dialog inside this one, so related books are text.
    with similar:
        st.markdown("**Readers also liked**")
        related_books(
            call_api(
                "GET",
                f"/books/{book_id}/similar",
                params={"top_k": 5, "other_authors_only": True},
                missing_ok=True,
            ),
            "No shared readers for this book yet.",
        )
    with by_author:
        st.markdown(f"**More by {plain(book['authors'][0])}**")
        related_books(
            call_api("GET", f"/books/{book_id}/by-author", params={"top_k": 5}, missing_ok=True),
            "No other books by this author in the catalog.",
        )
    st.caption(BOOK_CREDIT)


def related_books(hits: list[dict] | None, empty: str) -> None:
    if not hits:
        if hits is not None:
            st.caption(empty)
        return
    for hit in hits:
        item = hit["book"]
        # "By <author>" repeats the heading of the author column; show the series instead.
        reasons = [r for r in hit.get("reasons", []) if not r.startswith("By ")]
        reason = f" · {reasons[0]}" if reasons else ""
        st.text(f"{item['title']} · ★ {item['average_rating']:.2f}{reason}")


# --- Book → cocktail pairing ----------------------------------------------------------------

PAIR_OPEN = "pair_open_"  # + book id: the dialog's pairing section is open
PAGE_ROUTES = "page_routes"  # url path -> registered page, for hand-offs
PAIRINGS_PATH = "/pairings/book-cocktail"


def pairing_available() -> bool:
    """Pairing needs the API to serve both books and cocktails."""
    served = served_domains(st.session_state.get("service_health", {}))
    return all(served.get(domain, {}).get("enabled") for domain in (BOOKS, COCKTAILS))


def toggle_pairing(book_id: int) -> None:
    key = f"{PAIR_OPEN}{book_id}"
    st.session_state[key] = not st.session_state.get(key, False)


def next_pairing(seed_key: str, sets: int) -> None:
    st.session_state[seed_key] = (st.session_state.get(seed_key, 0) + 1) % max(sets, 1)


def open_drink(drink_id: int) -> None:
    """Show a recipe from inside a dialog, which cannot open another dialog: switch to the
    Cocktails page, which opens it."""
    st.session_state[OPEN_DRINK] = drink_id
    st.session_state["domain"] = COCKTAILS
    page = st.session_state.get(PAGE_ROUTES, {}).get("cocktails")
    if page is not None:
        st.switch_page(page)
    st.rerun()


def pairing_tastes() -> dict:
    """Remembered tastes that apply to pairings: avoided genres and the cocktail tastes."""
    body: dict = {}
    if avoided_genres := reading_memory().get("avoided_genres"):
        body["avoided_genres"] = avoided_genres
    if tastes := {key: value for key, value in cocktail_memory().items() if value}:
        body["cocktail_preferences"] = tastes
    return body


def pairing_card(result: dict, *, key: str, compact: bool = False, in_dialog: bool = False) -> None:
    """Drinks with photos, alcohol status and the pairer's reasons."""
    st.caption(plain(result["profile"]["summary"]))
    pairings = result["pairings"]
    if not pairings:
        free = result["profile"].get("alcohol_free")
        st.info(
            "No alcohol-free recipe fits this profile yet. Try another book."
            if free
            else "No recipe fits this profile yet. Try another book."
        )
        return
    saved = {item["drink_id"] for item in cocktail_shortlist()}
    columns = [st.container() for _ in pairings] if compact else st.columns(len(pairings))
    for column, item in zip(columns, pairings, strict=True):
        cocktail = item["cocktail"]
        did = cocktail["drink_id"]
        with column, st.container(key=f"pairing_drink_{key}_{did}"):
            with st.container(horizontal=compact, vertical_alignment="center", gap="small"):
                cocktail_image(
                    cocktail, width=64 if compact else "stretch", key=f"pair_{key}_{did}"
                )
                with st.container(gap=None):
                    st.text(cocktail["name"])
                    alcohol_badge(cocktail)
            for reason in item["reasons"]:
                st.caption(plain(reason))
            with st.container(horizontal=True, gap="small"):
                if st.button(
                    "Recipe",
                    key=f"pairing_recipe_{key}_{did}",
                    icon=":material/local_bar:",
                    type="tertiary",
                    help=f"Open the {cocktail['name']} recipe",
                ):
                    (open_drink if in_dialog else cocktail_details)(did)
                st.button(
                    "Saved" if did in saved else "Save",
                    key=f"pairing_save_{key}_{did}",
                    icon=":material/favorite:" if did in saved else ":material/favorite_border:",
                    type="tertiary",
                    disabled=did in saved,
                    on_click=add_cocktail,
                    args=(cocktail,),
                )
    st.caption(plain(result["note"]))


def pairing_panel(body: dict, *, key: str, in_dialog: bool = False) -> None:
    """Pairings for `body` (a book or a reading list) with an alcohol-free toggle and
    "Another pairing", which cycles through the pairer's sets."""
    seed_key = f"pair_seed_{key}"
    alcohol_free = st.toggle(
        "Alcohol-free only",
        key=f"pair_alcohol_free_{key}",
        help="Children's and young-adult books always get alcohol-free drinks.",
    )
    request = (
        body
        | pairing_tastes()
        | {
            "alcohol_free": alcohol_free,
            "seed": st.session_state.get(seed_key, 0),
        }
    )
    with st.spinner("Pouring…"):
        result = call_api("POST", PAIRINGS_PATH, json=request)
    if not result:
        return
    pairing_card(result, key=key, in_dialog=in_dialog)
    st.button(
        "Another pairing",
        key=f"pair_another_{key}",
        icon=":material/refresh:",
        disabled=result.get("sets", 1) < 2,
        help="Another set of drinks for the same profile",
        on_click=next_pairing,
        args=(seed_key, result.get("sets", 1)),
    )


def reading_list_pairing() -> None:
    """The Books page card: drinks for the reading list and remembered reading tastes."""
    memory = reading_memory()
    books = [b["book_id"] for b in reading_list()][:MAX_SHORTLIST]
    liked = memory.get("liked_genres", [])
    if not pairing_available() or not (books or liked):
        return
    with st.container(key="pairing_reading_list", border=True):
        st.subheader("Your reading list's cocktail", anchor=False)
        pairing_panel({"book_ids": books, "liked_genres": liked}, key="reading_list")


def book_note(health: dict) -> str:
    count = health.get("book_count")
    books = f"{count:,} books · " if count else ""
    return f"{BOOK_SOURCE} · {books}{BOOK_RATINGS} · {COVER_CREDIT}"
