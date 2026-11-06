"""Cocktail pages: recipe search, "What can I make?", recipe and compare dialogs, and the
cocktail chat cards and session panel."""

import re
from collections.abc import Callable

import streamlit as st

from curio.app.ui.client import call_api
from curio.app.ui.common import (
    COCKTAIL_COPY,
    COCKTAILS,
    GRID_COLUMNS,
    MAX_SHORTLIST,
    MAX_TASTES,
    RECIPE_CREDIT,
    approval_toggles,
    merge_newest,
    plain,
    queue_message,
    remember_input,
    restore_input,
)


def cocktail_shortlist() -> list[dict]:
    return st.session_state.setdefault("cocktail_shortlist", [])


def add_cocktail(cocktail: dict, announce: bool = True) -> bool:
    """Add a drink unless it is saved already or the list is full; return whether it was added."""
    items = cocktail_shortlist()
    if any(item["drink_id"] == cocktail["drink_id"] for item in items):
        return False
    if len(items) >= MAX_SHORTLIST:
        if announce:
            st.toast(f"Your shortlist holds up to {MAX_SHORTLIST} drinks.", icon=":material/info:")
        return False
    items.append(cocktail)
    if announce:
        st.toast(f"Saved {cocktail['name']} to your shortlist.", icon=":material/favorite:")
    return True


def remove_cocktail(drink_id: int) -> None:
    st.session_state["cocktail_shortlist"] = [
        c for c in cocktail_shortlist() if c["drink_id"] != drink_id
    ]


def cocktail_memory() -> dict:
    return st.session_state.setdefault("cocktail_preferences", {})


def forget_taste(field: str, value: object) -> None:
    memory = cocktail_memory()
    if field == "alcohol_free":
        memory["alcohol_free"] = False
    else:
        memory[field] = [item for item in memory.get(field, []) if item != value]


def remember_tastes(tastes: dict) -> list:
    """Store approved tastes; return the older values dropped to stay within the limits."""
    memory = cocktail_memory()
    if tastes.get("alcohol_free"):
        memory["alcohol_free"] = True
    dropped = []
    for field in ("liked_ingredients", "avoided_ingredients"):
        memory[field], lost = merge_newest(memory.get(field, []), tastes.get(field, []), MAX_TASTES)
        dropped += lost
    return dropped


def show_chat_cocktails(cocktails: list[dict], index: int, context: str) -> None:
    saved = {item["drink_id"] for item in cocktail_shortlist()}
    compact = context == "popup"
    columns = None if compact else st.columns(min(5, max(3, len(cocktails))))
    for position, cocktail in enumerate(cocktails):
        did = cocktail["drink_id"]
        slot = st.container() if compact else columns[position % len(columns)]
        with (
            slot,
            st.container(
                key=f"chat_cocktail_{context}_{index}_{did}",
                horizontal=compact,
                vertical_alignment="center" if compact else "top",
            ),
        ):
            cocktail_image(cocktail, width=56 if compact else "stretch")
            with st.container(gap=None):
                st.text(cocktail["name"])
                st.caption(f"{alcohol_label(cocktail)} · {cocktail['glass']}")
            with st.container(horizontal=True, gap="small"):
                if st.button("View", key=f"chat_drink_view_{context}_{index}_{did}"):
                    cocktail_details(did)
                st.button(
                    "Saved" if did in saved else "Save",
                    key=f"chat_drink_save_{context}_{index}_{did}",
                    icon=":material/favorite:" if did in saved else ":material/favorite_border:",
                    type="tertiary",
                    disabled=did in saved,
                    on_click=add_cocktail,
                    args=(cocktail,),
                )


def cocktail_context() -> dict:
    context: dict = {"domain": COCKTAILS}
    memory = {key: value for key, value in cocktail_memory().items() if value}
    if memory:
        context["cocktail_preferences"] = memory
    if cocktail_shortlist():
        context["cocktail_shortlist"] = [c["drink_id"] for c in cocktail_shortlist()]
    return context


def taste_chips(memory: dict) -> list[tuple[str, object, str]]:
    """Remembered cocktail tastes as (field, value, label) rows."""
    chips: list[tuple[str, object, str]] = []
    if memory.get("alcohol_free"):
        chips.append(("alcohol_free", True, "Alcohol-free"))
    chips += [("liked_ingredients", v, f"Likes {v}") for v in memory.get("liked_ingredients", [])]
    chips += [
        ("avoided_ingredients", v, f"Avoids {v}") for v in memory.get("avoided_ingredients", [])
    ]
    return chips


def cocktail_session_panel() -> None:
    copy = COCKTAIL_COPY
    with st.container(key="agent_session", border=True):
        st.subheader("Your session", anchor=False)
        approval_toggles()

        st.markdown("**Memory**")
        chips = taste_chips(cocktail_memory())
        if not chips:
            st.caption(copy.memory_hint)
        with st.container(horizontal=True, key="session_memory"):
            for field, value, label in chips:
                st.button(
                    label,
                    key=f"forget_cocktail_{field}_{value}",
                    icon=":material/close:",
                    help="Forget this taste",
                    on_click=forget_taste,
                    args=(field, value),
                )
        st.caption("Avoided ingredients filter listed recipe ingredients only, not allergens.")

        items = cocktail_shortlist()
        st.markdown(f"**Shortlist · {len(items)}**")
        if not items:
            st.caption(copy.shortlist_hint)
        for cocktail in items:
            with st.container(
                key=f"cocktail_shortlist_{cocktail['drink_id']}",
                horizontal=True,
                vertical_alignment="center",
            ):
                cocktail_image(cocktail, width=40)
                with st.container(gap=None, width="stretch"):
                    st.text(cocktail["name"])
                    st.caption(alcohol_label(cocktail))
                st.button(
                    "Remove",
                    icon=":material/close:",
                    key=f"cocktail_shortlist_remove_{cocktail['drink_id']}",
                    help=f"Remove {cocktail['name']}",
                    type="tertiary",
                    on_click=remove_cocktail,
                    args=(cocktail["drink_id"],),
                )
        if len(items) >= 2:
            with st.container(horizontal=True):
                if st.button(
                    "Compare", icon=":material/compare:", key="cocktail_shortlist_compare"
                ):
                    compare_cocktails_dialog([c["drink_id"] for c in items])
                st.button(
                    "Ask Curio",
                    icon=":material/chat_bubble:",
                    key="cocktail_shortlist_ask",
                    on_click=queue_message,
                    args=(copy.compare_prompt,),
                )


ALCOHOL_FILTERS = {"any": "Any", "alcoholic": "Alcoholic", "non_alcoholic": "Alcohol-free"}
# Catalog `alcoholic` values -> (label, badge colour).
ALCOHOL_STATUS = {
    "Alcoholic": ("Alcoholic", "orange"),
    "Non alcoholic": ("Alcohol-free", "green"),
    # Not alcohol-free: these recipes list alcoholic ingredients (see Cocktail.alcohol_free).
    "Optional alcohol": ("Alcohol optional", "blue"),
}
COCKTAIL_EXAMPLES = ["Margarita", "Something with rum and lime", "Creamy coffee"]
PANTRY_TOP_K = 9
SEARCH_TOP_K = 12


def alcohol_label(cocktail: dict) -> str:
    return ALCOHOL_STATUS.get(cocktail["alcoholic"], (cocktail["alcoholic"], "gray"))[0]


def alcohol_badge(cocktail: dict) -> None:
    label, color = ALCOHOL_STATUS.get(cocktail["alcoholic"], (cocktail["alcoholic"], "gray"))
    st.badge(label, color=color, icon=":material/local_bar:")


def cocktail_image(cocktail: dict, width: int | str = "stretch", *, key: str = "") -> None:
    """The browser loads recipe photos straight from TheCocktailDB; only https is shown.

    Full-width slots without a photo get a `missing_photo_<key>` placeholder.
    """
    url = cocktail.get("image_url")
    if url and url.startswith("https://"):
        # The caption names the photo for screen readers; ui.css hides it visually.
        st.image(url, caption=cocktail["name"], width=width)
    elif key:
        with st.container(key=f"missing_photo_{key}"):
            st.caption("No photo available.", text_alignment="center")


def ingredient_lines(ingredients: list[dict]) -> str:
    """A markdown bullet list of measures and names, kept literal."""
    lines = []
    for item in ingredients:
        measure = (item.get("measure") or "").strip()
        text = f"{measure} {item['name']}" if measure else item["name"]
        lines.append(f"- {plain(text)}")
    return "\n".join(lines)


def show_cocktails(
    cocktails: list[dict],
    *,
    context: str,
    annotate: Callable[[int], None] | None = None,
) -> None:
    saved = {item["drink_id"] for item in cocktail_shortlist()}
    for start in range(0, len(cocktails), GRID_COLUMNS):
        columns = st.columns(GRID_COLUMNS)
        for offset, cocktail in enumerate(cocktails[start : start + GRID_COLUMNS]):
            i = start + offset
            did = cocktail["drink_id"]
            with (
                columns[offset],
                st.container(key=f"cocktail_card_{context}_{did}_{i}", height="stretch"),
            ):
                cocktail_image(cocktail, key=f"{context}_drink_{did}_{i}")
                with st.container(key=f"cocktail_info_{context}_{did}_{i}", gap=None):
                    st.text(cocktail["name"])
                    st.caption(f"{cocktail['category']} · {cocktail['glass']}")
                with st.container(
                    horizontal=True,
                    vertical_alignment="center",
                    horizontal_alignment="distribute",
                ):
                    alcohol_badge(cocktail)
                    st.button(
                        "Saved" if did in saved else "Save",
                        key=f"cocktail_save_{context}_{did}_{i}",
                        icon=":material/favorite:"
                        if did in saved
                        else ":material/favorite_border:",
                        type="tertiary",
                        disabled=did in saved,
                        on_click=add_cocktail,
                        args=(cocktail,),
                    )
                if annotate is not None:
                    annotate(i)
                st.space("stretch")
                if st.button(
                    f"View {cocktail['name']}",
                    key=f"cocktail_view_{context}_{did}_{i}",
                    width="stretch",
                ):
                    cocktail_details(did)


def show_cocktail_hits(hits: list[dict] | None, *, context: str) -> None:
    if not hits:
        if hits is not None:
            st.info("No matching drinks. Try fewer ingredients or another category.")
        return

    def reason(i: int) -> None:
        if hits[i].get("reasons"):
            st.caption(plain(hits[i]["reasons"][0]))

    show_cocktails([hit["cocktail"] for hit in hits], context=context, annotate=reason)


def ingredient_chips(names: list[str], color: str, icon: str) -> str:
    safe = (re.sub(r"[\[\]]", "", name) for name in names)
    return " ".join(f":{color}-badge[{icon} {name}]" for name in safe)


def show_pantry_matches(matches: list[dict] | None) -> None:
    if not matches:
        if matches is not None:
            st.info("No recipes match yet. Allow more missing ingredients or add what you have.")
        return

    def coverage(i: int) -> None:
        match = matches[i]
        have, missing = match["have"], match["missing"]
        st.progress(
            match["coverage"],
            text=f"You have {len(have)} of {len(have) + len(missing)} ingredients",
        )
        st.markdown(ingredient_chips(have, "green", ":material/check:"))
        if missing:
            st.markdown(ingredient_chips(missing, "gray", ":material/add:"))
        else:
            st.caption("You can make this now.")

    show_cocktails([m["cocktail"] for m in matches], context="pantry", annotate=coverage)


def use_cocktail_example(query: str) -> None:
    st.session_state["cocktail_query"] = query


def cocktail_search_panel(ingredients: list[str]) -> None:
    categories = call_api("GET", "/cocktails/categories") or []
    with st.container(key="cocktail_search_panel", border=True):
        st.subheader("Find a recipe")
        with st.form("cocktail_search", border=False):
            restore_input("cocktail_query")
            query = st.text_input(
                "Describe a drink",
                key="cocktail_query",
                placeholder="e.g. fresh and minty, with lime",
                max_chars=200,
            )
            remember_input("cocktail_query", query)
            left, middle, right = st.columns([0.42, 0.33, 0.25], vertical_alignment="bottom")
            with left:
                restore_input("cocktail_ingredients")
                required = st.multiselect(
                    "Must include",
                    ingredients,
                    key="cocktail_ingredients",
                    max_selections=10,
                    placeholder="Any ingredients",
                )
                remember_input("cocktail_ingredients", required)
            with middle:
                alcohol = st.segmented_control(
                    "Alcohol",
                    list(ALCOHOL_FILTERS),
                    format_func=ALCOHOL_FILTERS.get,
                    default="any",
                    required=True,
                    key="cocktail_alcohol",
                )
            with right:
                category = st.selectbox(
                    "Category",
                    [None, *categories],
                    format_func=lambda c: c or "All",
                    key="cocktail_category",
                )
            st.form_submit_button(
                "Find drinks", type="primary", key="cocktail_search_submit", width="stretch"
            )
            st.caption("Or try an example")
            with st.container(key="cocktail_example", horizontal=True):
                for index, example in enumerate(COCKTAIL_EXAMPLES):
                    st.form_submit_button(
                        example,
                        key=f"cocktail_example_{index}",
                        on_click=use_cocktail_example,
                        args=(example,),
                    )
    alcohol = alcohol or "any"
    if not (query.strip() or required or alcohol != "any" or category):
        return
    body = {
        "query": query.strip(),
        "ingredients": required,
        "alcohol": alcohol,
        "category": category,
        "top_k": SEARCH_TOP_K,
    }
    with st.container(key="cocktail_search_results"):
        st.subheader("Matching drinks")
        with st.spinner("Searching recipes…"):
            show_cocktail_hits(call_api("POST", "/cocktails/search", json=body), context="search")


def pantry_panel(ingredients: list[str]) -> None:
    with st.container(key="pantry_panel", border=True):
        st.subheader("What can I make?")
        st.caption("Pick what you have. Curio finds drinks you can make now or almost.")
        restore_input("pantry_ingredients")
        pantry = st.multiselect(
            "Ingredients you have",
            ingredients,
            key="pantry_ingredients",
            max_selections=15,
            placeholder="e.g. Gin, Lime, Tonic water",
        )
        remember_input("pantry_ingredients", pantry)
        left, right = st.columns(2, vertical_alignment="bottom")
        with left:
            max_missing = st.slider(
                "Missing ingredients allowed", 0, 3, 2, key="pantry_max_missing"
            )
        with right:
            alcohol = st.segmented_control(
                "Alcohol",
                list(ALCOHOL_FILTERS),
                format_func=ALCOHOL_FILTERS.get,
                default="any",
                required=True,
                key="pantry_alcohol",
            )
    if not pantry:
        st.caption("Add at least one ingredient to see what you can make.")
        return
    body = {
        "ingredients": pantry,
        "alcohol": alcohol or "any",
        "max_missing": max_missing,
        "top_k": PANTRY_TOP_K,
    }
    with st.container(key="pantry_results"):
        st.subheader("Drinks you can make")
        with st.spinner("Checking recipes…"):
            show_pantry_matches(call_api("POST", "/cocktails/pantry", json=body))


def cocktails_page() -> None:
    if (drink_id := st.session_state.pop(OPEN_DRINK, None)) is not None:
        cocktail_details(drink_id)  # handed over from a book's pairing
    st.title("Cocktails")
    count = st.session_state.get("service_health", {}).get("cocktail_count")
    st.caption(
        f"Search {count:,} recipes by flavour or ingredient, or start from what you have."
        if count
        else "Search recipes by flavour or ingredient, or start from what you have."
    )
    mode = st.segmented_control(
        "Find drinks by",
        ["search", "pantry"],
        format_func={"search": "Search recipes", "pantry": "What can I make?"}.get,
        default="search",
        required=True,
        key="cocktail_mode",
        label_visibility="collapsed",
    )
    ingredients = call_api("GET", "/cocktails/ingredients")
    if ingredients is not None:
        (pantry_panel if mode == "pantry" else cocktail_search_panel)(ingredients)
    # The app footer credits TheCocktailDB on every cocktail-domain page.


@st.dialog("Recipe", width="large")
def cocktail_details(drink_id: int) -> None:
    cocktail = call_api("GET", f"/cocktails/{drink_id}")
    if cocktail is None:
        return
    saved = any(c["drink_id"] == drink_id for c in cocktail_shortlist())
    with st.container(key="cocktail_detail_body"):
        left, right = st.columns([0.4, 0.6], gap="large")
        with left:
            cocktail_image(cocktail, key=f"detail_drink_{drink_id}")
        with right:
            st.subheader(cocktail["name"], anchor=False)
            alcohol_badge(cocktail)
            st.caption(f"{cocktail['category']} · {cocktail['glass']}")
            st.markdown("**Ingredients**")
            st.markdown(ingredient_lines(cocktail["ingredients"]))
            st.markdown("**Method**")
            st.text(cocktail["instructions"] or "No instructions are listed.")
            with st.container(horizontal=True):
                st.button(
                    "Saved" if saved else "Save to shortlist",
                    icon=":material/favorite:" if saved else ":material/favorite_border:",
                    key=f"cocktail_detail_save_{drink_id}",
                    disabled=saved,
                    on_click=add_cocktail,
                    args=(cocktail,),
                )
                if st.button(
                    "Ask Curio about it",
                    icon=":material/chat_bubble:",
                    key=f"cocktail_detail_ask_{drink_id}",
                ):
                    # Dialogs rerun on their own; a full rerun closes this one and opens chat.
                    queue_message(
                        f"Tell me about the {cocktail['name']} (drink {drink_id}).",
                        True,
                        domain=COCKTAILS,
                    )
                    st.rerun()
            st.caption(RECIPE_CREDIT)
    st.markdown("**Similar drinks**")
    hits = call_api(
        "GET",
        f"/cocktails/{drink_id}/similar",
        params={"top_k": 5, "alcohol": "any"},
        missing_ok=True,
    )
    if hits:
        # Similar-drink buttons cannot open a second native dialog inside this one.
        for hit in hits:
            item = hit["cocktail"]
            reason = f" · {hit['reasons'][0]}" if hit.get("reasons") else ""
            st.text(f"{item['name']} · {alcohol_label(item)}{reason}")
    elif hits is not None:
        st.info("No similar drinks are available yet.")


@st.dialog("Compare drinks", width="large")
def compare_cocktails_dialog(drink_ids: list[int]) -> None:
    cocktails = [c for c in cocktail_shortlist() if c["drink_id"] in drink_ids][:4]
    if len(cocktails) < 2:
        st.info("Save at least two drinks to compare them.")
        return
    names = [{i["name"].casefold() for i in c["ingredients"]} for c in cocktails]
    shared = sorted(set.intersection(*names))
    if shared:
        st.caption(f"In every recipe: {plain(', '.join(shared))}")
    for column, cocktail in zip(st.columns(len(cocktails)), cocktails, strict=True):
        with column, st.container(key=f"compare_cocktail_{cocktail['drink_id']}"):
            cocktail_image(cocktail, key=f"compare_drink_{cocktail['drink_id']}")
            st.text(cocktail["name"])
            alcohol_badge(cocktail)
            st.caption(f"{plain(cocktail['category'])} · {plain(cocktail['glass'])}")
            st.markdown(ingredient_lines(cocktail["ingredients"]))
    st.caption(RECIPE_CREDIT)


OPEN_DRINK = "open_drink"  # a drink the Cocktails page opens (handed over from a dialog)


def cocktail_note(health: dict) -> str:
    count = health.get("cocktail_count")
    recipes = f"{count:,} recipes · " if count else ""
    return f"Recipe data: TheCocktailDB · {recipes}Please drink responsibly."
