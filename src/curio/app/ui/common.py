"""Shared UI state and helpers: the domain registry, per-domain copy, state keys and
small rendering helpers every page uses."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import streamlit as st


def restore_input(key: str) -> None:
    """Re-seed a keyed input Streamlit dropped while its panel was hidden."""
    if key not in st.session_state and f"saved_{key}" in st.session_state:
        st.session_state[key] = st.session_state[f"saved_{key}"]


def remember_input(key: str, value: object) -> None:
    st.session_state[f"saved_{key}"] = value


# --- Domains -------------------------------------------------------------------------------

# Each domain's client-side wiring (label, state keys, copy, pages, session panel, chat
# context) is one `DomainView` entry in `DOMAIN_VIEWS`, which `shell` fills.
# Which domains a server actually serves comes from /health (`domains`).
FASHION = "fashion"
COCKTAILS = "cocktails"
BOOKS = "books"
EVENING = "evening"
EVENING_UNAVAILABLE = (
    "Plan my evening needs the book catalog and the cocktail recipes on this server (run "
    "scripts.prepare_books and scripts.prepare_cocktails)."
)
BOOKS_UNAVAILABLE = (
    "The book catalog is not prepared on this server yet (run scripts.prepare_books)."
)
COCKTAILS_UNAVAILABLE = (
    "Cocktail recipes are not prepared on this server yet (run scripts.prepare_cocktails)."
)
FASHION_UNAVAILABLE = (
    "The H&M fashion data is not prepared on this server (see the technical guide's "
    "fashion data steps)."
)
LLM_HINT = "set LLM_MODEL (and LLM_API_KEY / LLM_BASE_URL) in .env, then restart the API."
RECIPE_CREDIT = "Recipe data: TheCocktailDB · Please drink responsibly."
BOOK_SOURCE = "Book data: goodbooks-10k from Goodreads (CC BY-SA 4.0)"
BOOK_RATINGS = "Ratings are Goodreads averages, not Curio's."
COVER_CREDIT = "Some covers: Open Library"
BOOK_CREDIT = f"{BOOK_SOURCE} · {BOOK_RATINGS}"
EVENING_CREDIT = (
    "Outfits: H&M research catalog · Books: goodbooks-10k (Goodreads ratings) · "
    "Drinks: TheCocktailDB · Please drink responsibly."
)


@dataclass(frozen=True)
class DomainCopy:
    title: str
    intro: str
    placeholder: str
    welcome: str
    starters: tuple[str, ...]
    unavailable: str
    popup_caption: str
    popup_note: str
    memory_hint: str
    shortlist_hint: str
    compare_prompt: str
    ask_memory: str
    ask_shortlist: str = "Ask before saving to my shortlist"


# The starter asks for an occasion; the agent asks which one, then builds the outfit.
OUTFIT_STARTER = "Outfit for an occasion"
FASHION_COPY = DomainCopy(
    title="Find your next favorite.",
    intro=(
        "Tell Curio your style, occasion or budget. It searches, compares and asks before "
        "changing anything you own."
    ),
    placeholder="Describe a piece or ask a follow-up…",
    welcome=(
        "Tell me what you're looking for, including a colour, occasion or budget. "
        "Try **white sneakers under €40**."
    ),
    starters=(
        "White sneakers under €40",
        "A linen shirt for summer",
        "Warm knitwear",
        OUTFIT_STARTER,
    ),
    unavailable=(
        "The assistant is currently unavailable. "
        "You can still discover pieces by description or photo."
    ),
    popup_caption="Find pieces. Refine by colour, style or budget.",
    popup_note="H&M research catalog · Prices are approximate. Current stock is unknown.",
    memory_hint="Nothing yet. Say “I always shop menswear” and Curio will ask to remember.",
    shortlist_hint="Save pieces from answers, or ask Curio to shortlist them.",
    compare_prompt="Compare the pieces in my shortlist and tell me which to pick.",
    ask_memory="Ask before remembering preferences",
)
COCKTAIL_COPY = DomainCopy(
    title="Mix something new tonight.",
    intro=(
        "Tell Curio a flavour, a mood or what's in your cupboard. It finds recipes, "
        "explains them and asks before changing anything you keep."
    ),
    placeholder="Describe a drink or ask a follow-up…",
    welcome=(
        "Tell me a flavour, an ingredient you have, or whether you'd like it alcohol-free. "
        "Try **something fresh with lime and mint**."
    ),
    starters=(
        "Something fresh with lime and mint",
        "Alcohol-free drinks",
        "What can I make with gin and tonic?",
    ),
    unavailable=(
        "The cocktail assistant is currently unavailable. "
        "You can still search recipes on the Cocktails page."
    ),
    popup_caption="Find drinks. Refine by ingredient, glass or alcohol.",
    popup_note=RECIPE_CREDIT,
    memory_hint="Nothing yet. Say “I don't drink alcohol” and Curio will ask to remember.",
    shortlist_hint="Save drinks from answers, or ask Curio to shortlist them.",
    compare_prompt="Compare the drinks in my shortlist and tell me which to make.",
    ask_memory="Ask before remembering tastes",
)


BOOK_COPY = DomainCopy(
    title="Find your next great read.",
    intro=(
        "Tell Curio a book you loved, a genre or a mood. It finds what similar readers "
        "enjoyed, explains why and asks before changing your reading list."
    ),
    placeholder="Describe a book or ask a follow-up…",
    welcome=(
        "Tell me a book you loved, a genre or a mood. Try **fantasy like The Name of the Wind**."
    ),
    starters=(
        "Fantasy like The Name of the Wind",
        "Short classic novels",
        "A cosy mystery",
        "Pair a cocktail with my reading list",
    ),
    unavailable=(
        "The book assistant is currently unavailable. "
        "You can still search and browse on the Books page."
    ),
    popup_caption="Find books. Refine by genre, mood or a book you loved.",
    popup_note=BOOK_CREDIT,
    memory_hint="Nothing yet. Say “I love fantasy, but no horror” and Curio will ask to remember.",
    shortlist_hint="Save books from answers, or ask Curio to add them to your reading list.",
    compare_prompt="Compare the books on my reading list and tell me which to read first.",
    ask_memory="Ask before remembering reading tastes",
    ask_shortlist="Ask before adding to my reading list",
)


EVENING_COPY = DomainCopy(
    title="Plan my evening.",
    intro=(
        "Tell Curio the occasion. It plans an outfit, a book and a drink paired with that book "
        "under one theme, explains why they go together and asks before saving anything."
    ),
    placeholder="Describe the evening or ask for a change…",
    welcome=(
        "Tell me the occasion and I'll plan the outfit, the book and the drink. Try **rainy "
        "Sunday in** or **date night under €100**."
    ),
    starters=("Rainy Sunday in", "Date night under €100", "Book club, alcohol-free"),
    unavailable=(
        "The evening assistant is currently unavailable. "
        "You can still plan on the Plan my evening page."
    ),
    popup_caption="Plan an evening. Swap a part, or save it all.",
    popup_note=EVENING_CREDIT,
    memory_hint="Nothing yet. Tastes you remember in Fashion, Books or Cocktails apply here.",
    shortlist_hint="Saving an evening adds each part to its own list.",
    compare_prompt="Plan my evening",
    ask_memory="Ask before remembering tastes",
    ask_shortlist="Ask before saving an evening",
)


def current_domain() -> str:
    """The selected domain; `main` moves it to the first served one when needed."""
    domain = st.session_state.get("domain")
    return domain if domain in DOMAIN_VIEWS else next(iter(DOMAIN_VIEWS))


def domain_view(domain: str | None = None) -> "DomainView":
    return DOMAIN_VIEWS[domain or current_domain()]


def domain_copy(domain: str | None = None) -> DomainCopy:
    return domain_view(domain).copy


def state_key(name: str, domain: str | None = None) -> str:
    return domain_view(domain).state[name]


def chat_history(domain: str | None = None) -> list[dict]:
    return st.session_state.setdefault(state_key("history", domain), [])


# --- Shared limits and helpers ----------------------------------------------------------

GRID_COLUMNS = 3
MAX_SHORTLIST = 20
MAX_TASTES = 10
# ShopperPreferences limits: memory beyond them would make every chat request fail (422).
PREFERENCE_LIMITS = {"sections": 3, "colours": 5}
# Grounding status -> (badge label, colour, icon), per domain.
type Badges = dict[str, tuple[str, str, str]]
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|~$])")


def plain(text: str) -> str:
    """Show model or catalog text literally inside Streamlit markdown."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def merge_newest(old: list, new: list, limit: int) -> tuple[list, list]:
    """Merge approved values into memory within the API's limit; the newest values win.

    Returns (kept, dropped). A value approved again moves to the newest end.
    """
    new = list(dict.fromkeys(new))
    merged = [value for value in old if value not in new] + new
    return merged[-limit:], merged[:-limit]


def queue_message(text: str, open_popup: bool = False, domain: str | None = None) -> None:
    st.session_state[state_key("queued", domain)] = text
    if open_popup:
        st.session_state["assistant_popup"] = True


def served_domains(health: dict) -> dict[str, dict]:
    """Each known domain's entry from /health: `enabled`, `chat_enabled`, `hint`, ...

    APIs older than 1.1 report no `domains`; they always served fashion and flagged cocktails
    with `cocktails_enabled`, and their cocktail chat status needs /domains (None here).
    """
    if "domains" in health:
        return {d["id"]: d for d in health["domains"] if d.get("id") in DOMAIN_VIEWS}
    return {
        FASHION: {"id": FASHION, "enabled": True, "chat_enabled": bool(health.get("chat_enabled"))},
        COCKTAILS: {
            "id": COCKTAILS,
            "enabled": bool(health.get("cocktails_enabled")),
            "chat_enabled": None,
        },
    }


def available_domains(health: dict) -> list[str]:
    """Served domains in registry order; the first one is the default."""
    served = served_domains(health)
    return [domain for domain in DOMAIN_VIEWS if served.get(domain, {}).get("enabled")]


def approval_toggles() -> None:
    st.markdown("**Approvals**")
    policy = st.session_state.setdefault("approval_policy", {})
    policy["ask_shortlist"] = st.toggle(
        domain_copy().ask_shortlist,
        value=policy.get("ask_shortlist", True),
        key="session_ask_shortlist",
    )
    policy["ask_memory"] = st.toggle(
        domain_copy().ask_memory,
        value=policy.get("ask_memory", True),
        key="session_ask_memory",
    )


# --- Domain registry -----------------------------------------------------------------------


@dataclass(frozen=True)
class DomainPage:
    page: Callable[[], None]
    title: str
    url_path: str
    in_menu: bool = True  # False: routable (deep links, hand-offs) but not in the menu


@dataclass(frozen=True)
class DomainView:
    """Everything the UI needs to show one domain. A new domain is one `DOMAIN_VIEWS` entry
    (plus its pages); the header switch, navigation, chat and session panel follow from it."""

    label: str  # header switch option
    unavailable: str  # caption beside the switch when the server does not serve the domain
    setup_hint: str  # its help text when the API gives no hint (an API older than 1.1)
    copy: DomainCopy
    state: Mapping[str, str]  # client-held session state keys
    badges: Badges
    approval_tools: Mapping[str, str]  # approval policy setting -> APPROVE tool
    feedback_prefix: str
    pages: tuple[DomainPage, ...]
    session_panel: Callable[[], None]
    chat_context: Callable[[], dict]  # this domain's fields of a chat request
    catalog_note: Callable[[dict], str]  # footer, from /health


# In display order: the first domain the server serves is the default. `shell` fills it.
DOMAIN_VIEWS: dict[str, DomainView] = {}
