"""Plan my evening: the page, its three cards with swaps, saving through an approval, and
the evening chat cards and session panel."""

import streamlit as st

from curio.app.ui.books import book_cover, reading_list, reading_memory
from curio.app.ui.client import call_api, product_photo
from curio.app.ui.cocktails import (
    alcohol_badge,
    alcohol_label,
    cocktail_image,
    cocktail_memory,
    cocktail_shortlist,
)
from curio.app.ui.common import (
    EVENING,
    EVENING_COPY,
    EVENING_CREDIT,
    plain,
    queue_message,
    served_domains,
)
from curio.app.ui.effects import apply_effects
from curio.app.ui.fashion import budget_line, preferences, product_name, shortlist

# The evening agent's latest plan state, sent back so it can swap or save its parts.
EVENING_STATE = "chat_evening"


EVENING_PLAN = "evening_plan"  # the Plan my evening page's current plan (the API's reply)
EVENING_OCCASIONS = (
    "Rainy Sunday in",
    "Date night",
    "Book club",
    "Summer garden party",
    "First date dinner",
    "Murder mystery night",
    "Game night with friends",
)
EVENING_AUDIENCES = {"women": "Women", "men": "Men"}
ANOTHER_BOOK = "Another book"
ANOTHER_DRINK = "Another drink"
ALCOHOL_FREE_DRINK = "Make the drink alcohol-free"
SAVE_EVENING = "Save the evening"


def evening_tastes() -> dict:
    """Remembered tastes from the other domains that shape an evening."""
    tastes: dict = {}
    if memory := {key: value for key, value in reading_memory().items() if value}:
        tastes["book_preferences"] = memory
    if memory := {key: value for key, value in cocktail_memory().items() if value}:
        tastes["cocktail_preferences"] = memory
    if (limit := preferences().get("max_price")) is not None:
        tastes["max_item_price"] = limit
    return tastes


def use_evening_occasion() -> None:
    """An occasion chip fills the occasion box."""
    if chosen := st.session_state.get("evening_occasion_chip"):
        st.session_state["evening_occasion"] = chosen


def plan_evening() -> None:
    occasion = (st.session_state.get("evening_occasion") or "").strip()
    if len(occasion) < 2:
        st.session_state["evening_error"] = "Tell Curio the occasion first, e.g. “date night”."
        return
    body: dict = {"occasion": occasion[:200], **evening_tastes()}
    if st.session_state.get("evening_use_budget"):
        body["budget"] = float(st.session_state.get("evening_budget") or 100)
    if audience := st.session_state.get("evening_audience"):
        body["audience"] = audience
    if st.session_state.get("evening_alcohol_free"):
        body["alcohol_free"] = True
    st.session_state.pop("evening_error", None)
    st.session_state["evening_request"] = body
    plan = call_api("POST", "/evening/plan", json=body)
    if plan:
        st.session_state[EVENING_PLAN] = plan


def swap_evening(part: str, slot: str | None = None, mode: str = "other") -> None:
    """Change one part of the page's plan through the API, which re-reads the rest."""
    plan = st.session_state.get(EVENING_PLAN)
    if not plan:
        return
    body: dict = {"evening": plan["state"], "part": part, "mode": mode}
    if slot is not None:
        body["slot"] = slot
    if tastes := evening_tastes():
        body["tastes"] = tastes
    changed = call_api("POST", "/evening/swap", json=body)
    if changed:
        st.session_state[EVENING_PLAN] = changed
        if changed.get("change"):
            st.toast(changed["change"], icon=":material/swap_horiz:")


def evening_parts(plan: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """The plan's pieces, book and drink, as cards."""
    outfit = (plan.get("outfit") or {}).get("outfit") or {}
    products = [s["product"] for s in outfit.get("slots", []) if s.get("product")]
    books = [plan["book"]["book"]] if plan.get("book") else []
    drinks = [plan["drink"]["cocktail"]] if plan.get("drink") else []
    return products, books, drinks


def save_evening_parts(parts: dict[str, list[int]], plan: dict) -> None:
    """Apply an approved evening: each part joins its own list, as the agent's effect does."""
    products, books, drinks = evening_parts(plan)
    apply_effects(
        {
            "products": products,
            "books": books,
            "cocktails": drinks,
            "applied": [{"kind": "evening", **parts}],
        }
    )


def open_save_evening() -> None:
    st.session_state["evening_saving"] = True


def close_save_evening() -> None:
    st.session_state.pop("evening_saving", None)


@st.dialog("Save this evening?", on_dismiss=close_save_evening)
def save_evening_dialog() -> None:
    """The page's approval step: every part is listed and can be unticked before saving."""
    plan = st.session_state.get(EVENING_PLAN)
    if not plan:
        st.caption("Plan an evening first.")
        return
    products, books, drinks = evening_parts(plan)
    st.caption("Curio saves each part to its own list. Untick anything you don't want.")
    kept: dict[str, list[int]] = {"product_ids": [], "book_ids": [], "drink_ids": []}
    for product in products:
        label = f"{product_name(product)} · €{product['price']:.2f} → shortlist"
        if st.checkbox(label, value=True, key=f"save_evening_product_{product['product_id']}"):
            kept["product_ids"].append(product["product_id"])
    for book in books:
        label = f"{book['title']} · {book['authors'][0]} → reading list"
        if st.checkbox(label, value=True, key=f"save_evening_book_{book['book_id']}"):
            kept["book_ids"].append(book["book_id"])
    for drink in drinks:
        label = f"{drink['name']} · {alcohol_label(drink)} → saved cocktails"
        if st.checkbox(label, value=True, key=f"save_evening_drink_{drink['drink_id']}"):
            kept["drink_ids"].append(drink["drink_id"])
    with st.container(horizontal=True):
        if st.button(
            "Approve",
            type="primary",
            icon=":material/check:",
            key="save_evening_approve",
            disabled=not any(kept.values()),
        ):
            save_evening_parts(kept, plan)
            close_save_evening()
            st.rerun()
        if st.button("Decline", icon=":material/close:", key="save_evening_decline"):
            close_save_evening()
            st.toast("Nothing was saved.", icon=":material/info:")
            st.rerun()


def evening_button(label: str, key: str, chat: bool, prompt: str, on_api, *args, **kw) -> None:
    """A swap button: on the planner page it calls the API, in chat it asks the agent."""
    if chat:
        st.button(label, key=key, type="tertiary", on_click=queue_message, args=(prompt,), **kw)
    else:
        st.button(label, key=key, type="tertiary", on_click=on_api, args=args, **kw)


def evening_outfit_card(plan: dict, key: str, chat: bool, active: bool) -> None:
    with st.container(key=f"evening_outfit_{key}", border=True):
        st.markdown(":material/checkroom: **Outfit**")
        part = plan.get("outfit")
        if not part:
            reason = next(
                (m["reason"] for m in plan.get("missing", []) if m["part"] == "outfit"), ""
            )
            st.caption(f":material/block: {plain(reason)}")
            return
        outfit = part["outfit"]
        for slot in outfit["slots"]:
            with st.container(
                key=f"evening_piece_{key}_{slot['slot']}",
                horizontal=True,
                vertical_alignment="center",
            ):
                product = slot.get("product")
                if product is None:
                    st.caption(f"**{slot['label']}**: {plain(slot.get('issue', ''))}")
                    continue
                if (photo := product_photo(product["product_id"])) is not None:
                    st.image(photo, width=56)
                with st.container(gap=None, width="stretch"):
                    st.text(product_name(product))
                    st.caption(f"{slot['label']} · €{product['price']:.2f} · {product['colour']}")
                    if active:
                        label = slot["label"].lower()
                        with st.container(horizontal=True, gap="small"):
                            evening_button(
                                "Swap", f"evening_swap_{key}_{slot['slot']}", chat,
                                f"Another {label} for my evening", swap_evening, "outfit",
                                slot["slot"], help=f"Another {label}",
                            )  # fmt: skip
                            evening_button(
                                "Cheaper", f"evening_cheaper_{key}_{slot['slot']}", chat,
                                f"Cheaper {label}", swap_evening, "outfit", slot["slot"],
                                "cheaper", help=f"A cheaper {label}",
                            )  # fmt: skip
        total, budget = outfit["total"], outfit.get("budget")
        if budget:
            st.progress(min(total / budget, 1.0), text=budget_line(total, budget))
        else:
            st.caption(budget_line(total, None))
        st.caption(plain(part["reason"]))
        for note in outfit.get("notes", []):
            st.caption(f":material/info: {plain(note)}")


def evening_book_card(plan: dict, key: str, chat: bool, active: bool) -> None:
    with st.container(key=f"evening_book_{key}", border=True):
        st.markdown(":material/menu_book: **Book**")
        part = plan.get("book")
        if not part:
            reason = next((m["reason"] for m in plan.get("missing", []) if m["part"] == "book"), "")
            st.caption(f":material/block: {plain(reason)}")
            return
        book = part["book"]
        book_cover(book, width=110, key=f"evening_{key}")
        st.text(book["title"])
        st.caption(plain(" · ".join([book["authors"][0], *book.get("genres", [])[:2]])))
        st.caption(plain(part["reason"]))
        if active:
            evening_button(
                ANOTHER_BOOK, f"evening_another_book_{key}", chat, ANOTHER_BOOK, swap_evening,
                "book", icon=":material/autorenew:",
            )  # fmt: skip


def evening_drink_card(
    plan: dict, key: str, chat: bool, active: bool, *, note: bool = True
) -> None:
    with st.container(key=f"evening_drink_{key}", border=True):
        st.markdown(":material/local_bar: **Drink**")
        part = plan.get("drink")
        if not part:
            reason = next(
                (m["reason"] for m in plan.get("missing", []) if m["part"] == "drink"), ""
            )
            st.caption(f":material/block: {plain(reason)}")
            return
        drink = part["cocktail"]
        cocktail_image(drink, width=110, key=f"evening_{key}")
        st.text(drink["name"])
        alcohol_badge(drink)
        st.caption(plain(part["reasons"][0]))
        if part.get("evening_fit"):
            st.caption(f"Suits the evening: {plain(', '.join(part['evening_fit']))}")
        if active:
            with st.container(horizontal=True, gap="small"):
                evening_button(
                    ANOTHER_DRINK, f"evening_another_drink_{key}", chat, ANOTHER_DRINK,
                    swap_evening, "drink", icon=":material/autorenew:",
                )  # fmt: skip
                evening_button(
                    "Alcohol-free", f"evening_alcohol_free_{key}", chat, ALCOHOL_FREE_DRINK,
                    swap_evening, "drink", None, "alcohol_free",
                    disabled=drink["alcoholic"] == "Non alcoholic",
                    help="An alcohol-free drink paired with the same book",
                )  # fmt: skip
        if note:
            st.caption(plain(part["note"]))


def says_why(text: str, plan: dict) -> bool:
    """Whether an answer already gives the plan's "why it works" line."""
    folded = text.casefold()
    return "why it works" in folded or plan["why"].casefold() in folded


def says_responsibly(text: str) -> bool:
    """Whether an answer already asks to drink responsibly."""
    return "responsibl" in text.casefold()


def evening_cards(
    plan: dict, *, key: str, chat: bool = False, active: bool = True, said: str = ""
) -> None:
    """The plan: its theme, three cards in a row (stacked in the chat column and on a
    phone), why it works, and Save evening. In chat the buttons ask the agent; on the page
    they call the API. `said` is the answer the cards come with: what it already says (why
    it works, drink responsibly) is not repeated on the cards."""
    theme = plan["theme"]
    with st.container(horizontal=True, vertical_alignment="center", key=f"evening_theme_{key}"):
        st.badge(theme["label"], icon=f":material/{theme['icon']}:", color="violet")
        evidence = theme["evidence"]
        st.caption(plain(f"{evidence[:1].upper()}{evidence[1:]}, so {theme['feeling']}."))
    if not says_why(said, plan):
        with st.container(key=f"evening_why_{key}"):
            st.markdown(f":material/auto_awesome: **Why it works** · {plain(plan['why'])}")
    note = not says_responsibly(said)
    if chat:
        # The chat column is narrow: one card under another reads better than three columns.
        evening_outfit_card(plan, key, chat, active)
        evening_book_card(plan, key, chat, active)
        evening_drink_card(plan, key, chat, active, note=note)
    else:
        # The outfit lists several pieces, so it gets the widest card.
        outfit, book, drink = st.columns([1.3, 1, 1], gap="medium")
        with outfit:
            evening_outfit_card(plan, key, chat, active)
        with book:
            evening_book_card(plan, key, chat, active)
        with drink:
            evening_drink_card(plan, key, chat, active, note=note)
    if active and plan.get("status") != "empty":
        if chat:
            st.button(
                "Save evening", key=f"evening_save_{key}", type="primary",
                icon=":material/favorite:", on_click=queue_message, args=(SAVE_EVENING,),
                help="Curio proposes saving every part; you approve or untick parts.",
            )  # fmt: skip
        else:
            st.button(
                "Save evening", key=f"evening_save_{key}", type="primary",
                icon=":material/favorite:", on_click=open_save_evening,
                help="Save the pieces, the book and the drink to your lists, after you approve.",
            )  # fmt: skip
            if st.session_state.get("evening_saving"):
                save_evening_dialog()


def evening_page() -> None:
    st.title("Plan my evening")
    st.caption(
        "Tell Curio the occasion. It picks one theme and plans an outfit, a book and a drink "
        "paired with that book, and says why they go together."
    )
    served = served_domains(st.session_state.get("service_health", {}))
    evening = served.get(EVENING, {})
    if evening.get("hint") and evening.get("enabled"):
        st.info(plain(evening["hint"]), icon=":material/info:")
    with st.container(key="evening_form", border=True):
        st.pills(
            "Occasion",
            EVENING_OCCASIONS,
            key="evening_occasion_chip",
            on_change=use_evening_occasion,
            label_visibility="collapsed",
        )
        st.text_input(
            "Occasion",
            key="evening_occasion",
            max_chars=200,
            placeholder="Describe the evening, e.g. “rainy Sunday in”",
        )
        with st.container(horizontal=True, vertical_alignment="bottom", key="evening_controls"):
            st.toggle("Outfit budget", key="evening_use_budget")
            st.number_input(
                "Budget (€)",
                min_value=10,
                max_value=5000,
                value=100,
                step=10,
                key="evening_budget",
                disabled=not st.session_state.get("evening_use_budget"),
                width=140,
            )
            st.segmented_control(
                "Outfit for",
                list(EVENING_AUDIENCES),
                format_func=EVENING_AUDIENCES.get,
                key="evening_audience",
            )
            st.toggle("Alcohol-free", key="evening_alcohol_free")
        st.button(
            "Plan my evening",
            type="primary",
            icon=":material/bedtime:",
            key="evening_plan_button",
            on_click=plan_evening,
        )
        if error := st.session_state.get("evening_error"):
            st.warning(error)
    plan = st.session_state.get(EVENING_PLAN)
    if plan:
        evening_cards(plan, key="page")
    else:
        st.caption("Pick an occasion above to see a plan.")


def evening_session_panel() -> None:
    copy = EVENING_COPY
    with st.container(key="agent_session", border=True):
        st.subheader("Your session", anchor=False)
        st.markdown("**Approvals**")
        policy = st.session_state.setdefault("approval_policy", {})
        policy["ask_shortlist"] = st.toggle(
            copy.ask_shortlist,
            value=policy.get("ask_shortlist", True),
            key="session_ask_shortlist",
        )
        st.markdown("**Memory**")
        tastes = [
            *(f"No {g}" for g in reading_memory().get("avoided_genres", [])),
            *(f"Likes {g}" for g in reading_memory().get("liked_genres", [])),
        ]
        if cocktail_memory().get("alcohol_free"):
            tastes.append("Alcohol-free drinks")
        if (limit := preferences().get("max_price")) is not None:
            tastes.append(f"Pieces up to €{limit:g}")
        st.caption(" · ".join(tastes) if tastes else copy.memory_hint)
        st.markdown("**Saved**")
        st.caption(
            f"{len(shortlist())} pieces · {len(reading_list())} books · "
            f"{len(cocktail_shortlist())} drinks"
        )
        st.caption(copy.shortlist_hint)


def evening_context() -> dict:
    """An evening reads every domain's tastes and lists, plus the current plan."""
    context: dict = {"domain": EVENING}
    for field, memory in (
        ("preferences", preferences()),
        ("book_preferences", reading_memory()),
        ("cocktail_preferences", cocktail_memory()),
    ):
        if values := {key: value for key, value in memory.items() if value}:
            context[field] = values
    if shortlist():
        context["shortlist"] = [p["product_id"] for p in shortlist()]
    if reading_list():
        context["book_shortlist"] = [b["book_id"] for b in reading_list()]
    if cocktail_shortlist():
        context["cocktail_shortlist"] = [c["drink_id"] for c in cocktail_shortlist()]
    if state := st.session_state.get(EVENING_STATE):
        context["evening"] = state
    return context


def evening_note(health: dict) -> str:
    return EVENING_CREDIT
