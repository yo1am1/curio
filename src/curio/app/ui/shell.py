"""The app shell: the domain registry entries, navigation, the header and its domain
switch, theming and the page router (`main`)."""

from collections.abc import Collection, Sequence
from pathlib import Path

import streamlit as st

from curio.app.ui.books import PAGE_ROUTES, book_context, book_note, book_session_panel, books_page
from curio.app.ui.chat import (
    BOOK_BADGES,
    COCKTAIL_BADGES,
    EVENING_BADGES,
    FASHION_BADGES,
    agent_home,
    assistant_popup,
    chat_tab,
)
from curio.app.ui.client import call_api
from curio.app.ui.cocktails import (
    cocktail_context,
    cocktail_note,
    cocktail_session_panel,
    cocktails_page,
)
from curio.app.ui.common import (
    BOOK_COPY,
    BOOKS,
    BOOKS_UNAVAILABLE,
    COCKTAIL_COPY,
    COCKTAILS,
    COCKTAILS_UNAVAILABLE,
    DOMAIN_VIEWS,
    EVENING,
    EVENING_COPY,
    EVENING_UNAVAILABLE,
    FASHION,
    FASHION_COPY,
    FASHION_UNAVAILABLE,
    DomainPage,
    DomainView,
    available_domains,
    current_domain,
    domain_view,
    plain,
    served_domains,
)
from curio.app.ui.evening import evening_context, evening_note, evening_page, evening_session_panel
from curio.app.ui.fashion import (
    discovery_page,
    evaluation_tab,
    fashion_context,
    fashion_note,
    fashion_session_panel,
    knowledge_graph_tab,
    recommendations_tab,
)

DOMAIN_VIEWS.update(
    {
        FASHION: DomainView(
            label=":material/checkroom: Fashion",
            unavailable="Fashion is not available",
            setup_hint=FASHION_UNAVAILABLE,
            copy=FASHION_COPY,
            # The fashion keys predate the domain switch and stay as they are.
            state={
                "history": "chat_history",
                "shortlist": "shortlist",
                "memory": "preferences",
                "queued": "chat_queued",
                "decision": "chat_decision",
            },
            badges=FASHION_BADGES,
            approval_tools={
                "ask_shortlist": "SaveToShortlist",
                "ask_memory": "RememberPreferences",
            },
            feedback_prefix="feedback",
            pages=(
                DomainPage(recommendations_tab, "Fashion", "fashion"),
                DomainPage(discovery_page, "Discover", "discover"),
                DomainPage(knowledge_graph_tab, "Product connections", "connections"),
                DomainPage(evaluation_tab, "Model quality", "quality", in_menu=False),
            ),
            session_panel=fashion_session_panel,
            chat_context=fashion_context,
            catalog_note=fashion_note,
        ),
        COCKTAILS: DomainView(
            label=":material/local_bar: Cocktails",
            unavailable="Cocktails are not available",
            setup_hint=COCKTAILS_UNAVAILABLE,
            copy=COCKTAIL_COPY,
            state={
                "history": "chat_history_cocktails",
                "shortlist": "cocktail_shortlist",
                "memory": "cocktail_preferences",
                "queued": "chat_queued_cocktails",
                "decision": "chat_decision_cocktails",
            },
            badges=COCKTAIL_BADGES,
            approval_tools={"ask_shortlist": "SaveCocktails", "ask_memory": "RememberTastes"},
            feedback_prefix="feedback_cocktails",
            pages=(DomainPage(cocktails_page, "Cocktails", "cocktails"),),
            session_panel=cocktail_session_panel,
            chat_context=cocktail_context,
            catalog_note=cocktail_note,
        ),
        BOOKS: DomainView(
            label=":material/menu_book: Books",
            unavailable="Books are not available",
            setup_hint=BOOKS_UNAVAILABLE,
            copy=BOOK_COPY,
            state={
                "history": "chat_history_books",
                "shortlist": "book_shortlist",
                "memory": "book_preferences",
                "queued": "chat_queued_books",
                "decision": "chat_decision_books",
            },
            badges=BOOK_BADGES,
            approval_tools={"ask_shortlist": "SaveBooks", "ask_memory": "RememberReadingTastes"},
            feedback_prefix="feedback_books",
            pages=(DomainPage(books_page, "Books", "books"),),
            session_panel=book_session_panel,
            chat_context=book_context,
            catalog_note=book_note,
        ),
        EVENING: DomainView(
            label=":material/bedtime: Evening",
            unavailable="Evening is not available",
            setup_hint=EVENING_UNAVAILABLE,
            copy=EVENING_COPY,
            state={
                "history": "chat_history_evening",
                "queued": "chat_queued_evening",
                "decision": "chat_decision_evening",
            },
            badges=EVENING_BADGES,
            approval_tools={"ask_shortlist": "SaveEvening"},
            feedback_prefix="feedback_evening",
            pages=(DomainPage(evening_page, "Plan my evening", "evening"),),
            session_panel=evening_session_panel,
            chat_context=evening_context,
            catalog_note=evening_note,
        ),
    }
)
# Pages that belong to one domain; a deep link to one of them selects that domain.
PAGE_DOMAINS = {
    page.url_path: domain for domain, view in DOMAIN_VIEWS.items() for page in view.pages
}
QUICK_START = "uv run python -m scripts.prepare_cocktails"


def switch_domain() -> None:
    st.session_state["domain"] = st.session_state["domain_switch"]
    st.session_state["domain_switched"] = True


# The domain a new visit opens on when the server serves it: Curio leads with the engine's
# composition showcase, Plan my evening.
DEFAULT_DOMAIN = EVENING
# Where each domain lands when chosen (or on a first visit): its own page, not the chat.
LANDING_PAGES = {EVENING: "evening"}
LANDED = "landed"  # set once a visit has landed, so later visits to Ask Curio stay there


def active_domain(available: Sequence[str]) -> str:
    """The selected domain; a new visit starts on Evening when it is served, and a selection
    the server does not serve falls back to the first served domain."""
    if "domain" not in st.session_state and DEFAULT_DOMAIN in available:
        st.session_state["domain"] = DEFAULT_DOMAIN
    if current_domain() not in available:
        st.session_state["domain"] = available[0]
    return current_domain()


def landing_page(domain: str, pages: Sequence) -> object:
    """The page a domain opens on: its landing page when it has one, else Ask Curio."""
    path = LANDING_PAGES.get(domain)
    return next((p for p in pages if path and p.url_path == path), pages[0])


def domain_switch(available: Sequence[str], served: dict[str, dict]) -> None:
    # The widget mirrors `domain`, which deep links and tests may set directly.
    st.session_state["domain_switch"] = current_domain()
    st.segmented_control(
        "Catalog",
        list(available),
        format_func=lambda domain: DOMAIN_VIEWS[domain].label,
        required=True,
        key="domain_switch",
        on_change=switch_domain,
        disabled=len(available) < 2,
        label_visibility="collapsed",
    )
    for domain, view in DOMAIN_VIEWS.items():
        if domain not in available:
            hint = served.get(domain, {}).get("hint") or view.setup_hint
            st.caption(view.unavailable, help=hint)


def domain_pages(domain: str, available: Collection[str]) -> list:
    """Every served domain's pages stay routable; only the active domain's are in the menu.
    Pages of a domain the server does not serve are not registered at all."""
    pages = [st.Page(agent_home, title="Ask Curio", default=True)]
    for owner, view in DOMAIN_VIEWS.items():
        if owner not in available:
            continue
        for spec in view.pages:
            shown = spec.in_menu and owner == domain
            pages.append(
                st.Page(
                    spec.page,
                    title=spec.title,
                    url_path=spec.url_path,
                    visibility="visible" if shown else "hidden",
                )
            )
    pages.append(st.Page(chat_tab, title="Assistant", url_path="assistant", visibility="hidden"))
    return pages


def setup_page() -> None:
    """Shown when the API serves no domain yet, instead of an empty app."""
    served = served_domains(st.session_state.get("service_health", {}))
    st.title("Almost ready.")
    st.caption("Curio's API is running, but no catalog is prepared on it yet.")
    st.markdown(
        "The quickest start is the cocktail catalog (about 400 KB). Prepare it, then "
        "restart the API:"
    )
    st.code(QUICK_START, language="bash")
    st.caption("`docker compose --profile demo up` and `scripts/quickstart.sh` do this for you.")
    for domain, view in DOMAIN_VIEWS.items():
        hint = served.get(domain, {}).get("hint") or view.setup_hint
        st.markdown(f"**{view.label}**: {plain(hint)}")


def catalog_note(health: dict) -> str:
    return domain_view().catalog_note(health)


def main() -> None:
    st.set_page_config(
        page_title="Curio · Trustworthy recommendation agents", page_icon="C", layout="wide"
    )
    st.html("<style>" + Path(__file__).with_name("ui.css").read_text() + "</style>")
    health = call_api("GET", "/health")
    header = st.container(
        key="curio_header",
        horizontal=True,
        vertical_alignment="center",
        horizontal_alignment="distribute",
    )
    header.html(
        '<div class="curio-brand">Curio<span>TRUSTWORTHY RECOMMENDATION AGENTS</span></div>'
    )
    if health is None:
        st.info("Curio cannot connect right now. Please try again shortly.")
        if st.button("Retry connection"):
            st.rerun()
        return
    st.session_state["service_health"] = health
    served = served_domains(health)
    available = available_domains(health)
    if not available:
        st.navigation([st.Page(setup_page, title="Get started", default=True)]).run()
        return
    domain = active_domain(available)
    pages = domain_pages(domain, available)
    st.session_state[PAGE_ROUTES] = {p.url_path: p for p in pages if p.url_path}
    page = st.navigation(pages, position="top")
    owner = PAGE_DOMAINS.get(page.url_path)
    switched = st.session_state.pop("domain_switched", False)
    first_visit = not st.session_state.get(LANDED)
    st.session_state[LANDED] = True
    if owner is not None and owner != domain:
        if switched:  # the shopper changed domain on another domain's page: its landing
            st.switch_page(landing_page(domain, pages))
        st.session_state["domain"] = owner  # a deep link selects its page's domain
        st.rerun()
    landing = landing_page(domain, pages)
    if page.title == "Ask Curio" and landing is not pages[0] and (switched or first_visit):
        st.switch_page(landing)  # Evening opens on Plan my evening, not on its chat
    with header:
        domain_switch(available, served)
    page.run()
    if page.title not in ("Ask Curio", "Assistant"):
        assistant_popup()
    else:
        # The conversation on this page already took any queued message; an "open popup"
        # request must not carry over and open the popover on the next page.
        st.session_state.pop("assistant_popup", None)
    with st.container(key="catalog_note"):
        st.caption(catalog_note(health))
