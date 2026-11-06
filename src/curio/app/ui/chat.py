"""The shared chat panel: streaming a turn, the step trace, grounding badges, approval
cards, feedback, applying approved effects, and the assistant popup."""

from collections.abc import Callable

import streamlit as st

from curio.app.ui.books import pairing_card, show_chat_books
from curio.app.ui.client import call_api, stream_chat
from curio.app.ui.cocktails import alcohol_label, show_chat_cocktails
from curio.app.ui.common import (
    EVENING,
    FASHION,
    LLM_HINT,
    Badges,
    DomainCopy,
    chat_history,
    current_domain,
    domain_copy,
    domain_view,
    plain,
    queue_message,
    served_domains,
    state_key,
)
from curio.app.ui.effects import apply_effects
from curio.app.ui.evening import EVENING_STATE, evening_cards
from curio.app.ui.fashion import OUTFIT_STATE, outfit_card, show_chat_products

STEP_LABELS = {
    "Understand request": "Plan",
    "SearchCatalog": "Search catalog",
    "InspectProducts": "Inspect products",
    "FindRelated": "Find related pieces",
    "PersonalPicks": "Personal picks",
    "Plan outfit": "Plan outfit",
    "Fill slot": "Fill slot",
    "BuildOutfit": "Build outfit",
    "SwapOutfitItem": "Swap a piece",
    "SearchCocktails": "Search recipes",
    "InspectCocktails": "Inspect recipes",
    "SimilarCocktails": "Find similar drinks",
    "WhatCanIMake": "Check your ingredients",
    "SearchBooks": "Search books",
    "InspectBooks": "Inspect books",
    "ReadersAlsoLiked": "Readers also liked",
    "BooksByAuthor": "More by the author",
    "AskShopper": "Question for you",
    "SaveToShortlist": "Shortlist proposal",
    "RememberPreferences": "Memory proposal",
    "SaveCocktails": "Shortlist proposal",
    "RememberTastes": "Memory proposal",
    "SaveBooks": "Reading list proposal",
    "PlanEvening": "Plan evening",
    "SwapEveningPart": "Swap a part",
    "SaveEvening": "Save evening proposal",
    "Evening theme": "Evening theme",
    "Evening outfit": "Outfit",
    "Evening book": "Book",
    "Evening drink": "Drink",
    "RememberReadingTastes": "Memory proposal",
    "Draft answer": "Draft answer",
    "Grounding check": "Grounding check",
    "Self-correction": "Self-correction",
    "Tool budget": "Tool budget",
    "Time budget": "Time budget",
}
STEP_ICONS = {
    "ok": ":material/check_circle:",
    "error": ":material/error:",
    "blocked": ":material/block:",
    "pending": ":material/hourglass_top:",
    "skipped": ":material/do_not_disturb_on:",
}
FASHION_BADGES: Badges = {
    "verified": ("Prices verified against the catalog", "green", ":material/verified:"),
    "repaired": ("Self-corrected to match the catalog", "orange", ":material/auto_fix_high:"),
    "fallback": ("Unverified answer replaced with catalog facts", "red", ":material/shield:"),
}
COCKTAIL_BADGES: Badges = {
    "verified": ("Checked against the recipes", "green", ":material/verified:"),
    "repaired": ("Self-corrected to match the recipes", "orange", ":material/auto_fix_high:"),
    "fallback": ("Unverified answer replaced with recipe facts", "red", ":material/shield:"),
}
BOOK_BADGES: Badges = {
    "verified": ("Titles and ratings checked against the catalog", "green", ":material/verified:"),
    "repaired": ("Self-corrected to match the catalog", "orange", ":material/auto_fix_high:"),
    "fallback": ("Unverified answer replaced with catalog facts", "red", ":material/shield:"),
}
EVENING_BADGES: Badges = {
    "verified": ("Prices, titles and drinks checked", "green", ":material/verified:"),
    "repaired": ("Self-corrected to match the catalogs", "orange", ":material/auto_fix_high:"),
    "fallback": ("Unverified answer replaced with catalog facts", "red", ":material/shield:"),
}
# Proposal arguments the shopper may narrow: list values can be dropped one by one.
APPROVAL_LIST_FIELDS = (
    "product_ids",
    "sections",
    "colours",
    "drink_ids",
    "liked_ingredients",
    "avoided_ingredients",
    "book_ids",
    "liked_genres",
    "avoided_genres",
    "avoided_authors",
)


def step_line(step: dict) -> str:
    label = STEP_LABELS.get(step["name"], step["name"])
    seconds = step.get("duration_ms", 0) / 1000
    timing = f" · {seconds:.1f}s" if seconds >= 0.1 else ""
    icon = STEP_ICONS.get(step.get("status", "ok"), "")
    return f"{icon} **{label}** · {plain(step['summary'])}{timing}"


def show_trace(steps: list[dict]) -> None:
    if not steps:
        return
    seconds = sum(step.get("duration_ms", 0) for step in steps) / 1000
    actions = sum(step["kind"] in ("tool", "approval") for step in steps)
    label = (
        f"How Curio worked · {actions} {'action' if actions == 1 else 'actions'} · {seconds:.1f}s"
    )
    with st.expander(label, icon=":material/account_tree:"):
        for step in steps:
            st.markdown(step_line(step))


def auto_approve(domain: str | None = None) -> list[str]:
    policy = st.session_state.get("approval_policy", {})
    return [
        tool
        for setting, tool in domain_view(domain).approval_tools.items()
        if not policy.get(setting, True)
    ]


def queue_decision(index: int, approved: bool, context: str) -> None:
    turn = chat_history()[index]
    action = turn["pending_action"]
    edits = None
    if approved:
        args = action["args"]

        def kept(field: str, value: object) -> bool:
            return st.session_state.get(f"approve_{context}_{index}_{field}_{value}", True)

        narrowed: dict = {
            field: [value for value in args.get(field, []) if kept(field, value)]
            for field in APPROVAL_LIST_FIELDS
            if field in args
        }
        if "max_price" in args and not kept("max_price", args["max_price"]):
            narrowed["max_price"] = None
        if args.get("alcohol_free") and not kept("alcohol_free", True):
            narrowed["alcohol_free"] = False
        edits = {
            field: value for field, value in narrowed.items() if value != args.get(field)
        } or None
    st.session_state[state_key("decision")] = {
        "index": index,
        "approved": approved,
        "edits": edits,
    }


def feedback_key(context: str, index: int) -> str:
    # Per-domain keys stop a rating on one domain's answer from carrying over to the other's.
    return f"{domain_view().feedback_prefix}_{context}_{index}"


def send_feedback(index: int, context: str) -> None:
    history = chat_history()
    value = st.session_state.get(feedback_key(context, index))
    if value is None or index >= len(history):
        return
    turn = history[index]
    question = next((t["content"] for t in reversed(history[:index]) if t["role"] == "user"), "")
    call_api(
        "POST",
        "/chat/feedback",
        json={
            "rating": "up" if value == 1 else "down",
            "message": question[:2000],
            "answer": turn["content"][:4000],
            "product_ids": [p["product_id"] for p in turn.get("products", [])][:5],
            "tools": [s["name"] for s in turn.get("steps", []) if s["kind"] == "tool"][:12],
        },
    )
    st.toast("Thanks for the feedback.", icon=":material/thumb_up:")


def approval_options(action: dict) -> list[tuple[str, object, str]]:
    """Checkbox rows (field, value, label) for every value the shopper may drop."""
    args = action["args"]
    products = {p["product_id"]: p for p in action.get("products") or []}
    cocktails = {c["drink_id"]: c for c in action.get("cocktails") or []}
    books = {b["book_id"]: b for b in action.get("books") or []}
    options: list[tuple[str, object, str]] = []
    for field in APPROVAL_LIST_FIELDS:
        for value in args.get(field) or []:
            if field == "product_ids":
                if value not in products:
                    continue
                label = f"{products[value]['name']} · €{products[value]['price']:.2f}"
            elif field == "drink_ids":
                if value not in cocktails:
                    continue
                label = f"{cocktails[value]['name']} · {alcohol_label(cocktails[value])}"
            elif field == "book_ids":
                if value not in books:
                    continue
                label = f"{books[value]['title']} · {books[value]['authors'][0]}"
            elif field in ("liked_ingredients", "liked_genres"):
                label = f"Likes {value}"
            elif field == "avoided_genres":
                label = f"No {value}"
            elif field == "avoided_authors":
                label = f"No books by {value}"
            elif field == "avoided_ingredients":
                label = f"Avoids {value}"
            else:
                label = str(value)
            options.append((field, value, label))
    if args.get("max_price") is not None:
        options.append(("max_price", args["max_price"], f"Budget up to €{args['max_price']:g}"))
    if args.get("alcohol_free"):
        options.append(("alcohol_free", True, "Alcohol-free drinks only"))
    return options


def approval_card(turn: dict, index: int, context: str, active: bool):
    """Render a proposal; an inactive card returns the slot holding its status caption."""
    action = turn["pending_action"]
    with st.container(key=f"approval_{context}_{index}", border=True):
        st.markdown(":material/how_to_reg: **Needs your approval**")
        st.caption(plain(action.get("summary", "")))
        if not active:
            status = st.empty()
            status.caption(turn.get("resolution", "No longer pending."))
            return status
        if turn.get("decision_error"):
            # The API did not accept the last decision: nothing was applied, so it can be retried.
            st.error(turn["decision_error"])
        for field, value, label in approval_options(action):
            st.checkbox(label, value=True, key=f"approve_{context}_{index}_{field}_{value}")
        with st.container(horizontal=True):
            st.button(
                "Approve",
                type="primary",
                icon=":material/check:",
                key=f"approve_{context}_{index}",
                on_click=queue_decision,
                args=(index, True, context),
            )
            st.button(
                "Decline",
                icon=":material/close:",
                key=f"decline_{context}_{index}",
                on_click=queue_decision,
                args=(index, False, context),
            )
        st.caption("Uncheck anything you don't want before approving.")
    return None


def show_chat_turn(turn: dict, index: int, context: str, latest: bool = False):
    """Render one turn; returns the status slot of an inactive approval card, if any."""
    status = None
    with st.chat_message(turn["role"]):
        if turn["role"] == "assistant":
            show_trace(turn.get("steps", []))
        st.markdown(turn["content"])
        grounding = (turn.get("grounding") or {}).get("status")
        badges = domain_view().badges
        if grounding in badges:
            label, color, icon = badges[grounding]
            st.badge(label, color=color, icon=icon)
        if evening := turn.get("evening"):
            with st.container(key=f"chat_evening_{context}_{index}"):
                evening_cards(
                    evening,
                    key=f"chat_{context}_{index}",
                    chat=True,
                    active=latest,
                    said=turn["content"],
                )
        products = [] if evening else turn.get("products") or []
        if outfit := turn.get("outfit"):
            outfit_card(outfit, index, context, latest)
            worn = {s["product"]["product_id"] for s in outfit["slots"] if s.get("product")}
            products = [p for p in products if p["product_id"] not in worn]
        if products:
            show_chat_products(products, index, context)
        if evening:
            pass
        elif turn.get("pairing"):
            with st.container(key=f"chat_pairing_{context}_{index}", border=True):
                pairing_card(
                    turn["pairing"], key=f"chat_{context}_{index}", compact=context == "popup"
                )
        elif turn.get("cocktails"):
            show_chat_cocktails(turn["cocktails"], index, context)
        if turn.get("books") and not evening:
            show_chat_books(turn["books"], index, context)
        if turn.get("pending_action"):
            status = approval_card(
                turn, index, context, active=latest and not turn.get("resolution")
            )
        if latest and (clarification := turn.get("clarification")):
            with st.container(horizontal=True, key=f"chat_options_{context}_{index}"):
                for number, option in enumerate(clarification["options"]):
                    st.button(
                        option,
                        key=f"chat_option_{context}_{index}_{number}",
                        on_click=queue_message,
                        args=(option,),
                    )
        if latest and turn.get("suggestions"):
            with st.container(horizontal=True, key=f"chat_suggestions_{context}_{index}"):
                for number, suggestion in enumerate(turn["suggestions"]):
                    st.button(
                        suggestion,
                        key=f"chat_suggestion_{context}_{index}_{number}",
                        type="tertiary",
                        icon=":material/arrow_forward:",
                        on_click=queue_message,
                        args=(suggestion,),
                    )
        if turn["role"] == "assistant" and turn.get("steps") and turn.get("status") == "complete":
            st.feedback(
                "thumbs",
                key=feedback_key(context, index),
                on_change=send_feedback,
                args=(index, context),
            )
    return status


def reset_chat() -> None:
    st.session_state[state_key("history")] = []
    if current_domain() == FASHION:
        st.session_state.pop(OUTFIT_STATE, None)
    if current_domain() == EVENING:
        st.session_state.pop(EVENING_STATE, None)
    for name in ("queued", "decision"):
        st.session_state.pop(state_key(name), None)


def close_assistant() -> None:
    st.session_state["assistant_popup"] = False


def history_item(turn: dict) -> dict:
    item = {"role": turn["role"], "content": turn["content"][:4000]}
    if turn.get("products"):
        item["product_ids"] = [p["product_id"] for p in turn["products"][:5]]
    if turn.get("cocktails"):
        item["drink_ids"] = [c["drink_id"] for c in turn["cocktails"][:5]]
    if turn.get("books"):
        item["book_ids"] = [b["book_id"] for b in turn["books"][:5]]
    return item


def chat_payload(history: list[dict]) -> dict:
    """Recent turns plus client-held session state; unset state and the default domain are
    left out, so fashion requests look exactly as they did before cocktails existed."""
    payload: dict = {"history": [history_item(turn) for turn in history[-10:]]}
    payload |= domain_view().chat_context()
    if policy := auto_approve():
        payload["auto_approve"] = policy
    return payload


def run_turn(
    payload: dict,
    history: list[dict],
    context: str,
    user_turn: dict | None,
    on_error: Callable[[str], object] | None = None,
) -> bool:
    """Stream one turn and append it to history; return whether the API answered."""
    progress = st.empty()
    with progress.container(), st.chat_message("assistant"):
        live = st.status("Curio is planning…", expanded=context != "popup")

        def on_step(step: dict) -> None:
            live.markdown(step_line(step))
            live.update(label=f"{STEP_LABELS.get(step['name'], step['name'])}…")

        reply = stream_chat(payload, on_step, on_error)
        if reply is None:
            live.update(label="Stopped", state="error", expanded=False)
            st.caption("Please send your message again to retry.")
            return False
    # The finished turn below repeats these steps in its collapsed trace.
    progress.empty()
    apply_effects(reply)
    assistant_turn = {
        "role": "assistant",
        "content": reply["text"],
        "products": reply.get("products", []),
        "cocktails": reply.get("cocktails", []),
        "books": reply.get("books", []),
        "steps": reply.get("steps", []),
        "status": reply.get("status", "complete"),
        "grounding": reply.get("grounding"),
        "clarification": reply.get("clarification"),
        "pending_action": reply.get("pending_action"),
        "suggestions": reply.get("suggestions", []),
        "outfit": reply.get("outfit"),
        "pairing": reply.get("pairing"),
        "evening": reply.get("evening"),
    }
    if reply.get("evening"):
        # Follow-ups ("another book", "save the evening") refer to the latest plan.
        st.session_state[EVENING_STATE] = reply["evening"]["state"]
    if reply.get("outfit"):
        # Follow-ups ("cheaper shoes", "save the outfit") refer to the latest outfit.
        st.session_state[OUTFIT_STATE] = reply["outfit"]["state"]
    if user_turn is not None:
        history.append(user_turn)
    history.append(assistant_turn)
    show_chat_turn(assistant_turn, len(history) - 1, context, latest=True)
    return True


def chat_available(domain: str, health: dict) -> bool:
    info = served_domains(health).get(domain)
    if not info or not info.get("enabled"):
        return False
    if info.get("chat_enabled") is None:  # an API older than 1.1
        domains = call_api("GET", "/domains") or []
        return any(item.get("id") == domain and item.get("chat_enabled") for item in domains)
    return bool(info["chat_enabled"])


def welcome(copy: DomainCopy, context: str) -> None:
    with st.chat_message("assistant"):
        st.markdown(copy.welcome)
        with st.container(horizontal=True, key=f"chat_starters_{context}"):
            for number, starter in enumerate(copy.starters):
                st.button(
                    starter,
                    key=f"chat_starter_{context}_{number}",
                    on_click=queue_message,
                    args=(starter,),
                )


def chat_conversation(context: str) -> None:
    copy = domain_copy()
    health = call_api("GET", "/health")
    if health is None:
        return
    if not chat_available(current_domain(), health):
        st.info(copy.unavailable)
        if health.get("llm_configured") is False:
            st.caption(f"To enable chat, {LLM_HINT}")
        return
    history = chat_history()
    queued = st.session_state.pop(state_key("queued"), None)
    decision = st.session_state.pop(state_key("decision"), None)
    # The chat input is rendered below the transcript, but its submitted value is already in
    # session state, so a first message gets the full-height transcript too.
    typed = st.session_state.get(f"chat_input_{context}")
    idle = not history and not queued and not typed and decision is None
    transcript = st.container(
        height=240 if context == "home" and idle else (360 if context == "popup" else 640),
        border=False,
        autoscroll=True,
        key=f"chat_log_{context}",
    )
    # Rendered below the transcript; its value is read now and the transcript filled after.
    message = st.chat_input(copy.placeholder, key=f"chat_input_{context}", max_chars=2000) or queued
    with transcript:
        if not history and not message:
            welcome(copy, context)
        payload = None
        decided = None
        if decision is not None and decision["index"] < len(history):
            decided = history[decision["index"]]
            action = decided.get("pending_action")
            if action and not decided.get("resolution"):
                payload = chat_payload(history)
                payload["decision"] = {
                    "action": action,
                    "approved": decision["approved"],
                    "edited_args": decision["edits"],
                }
        busy = bool(message) or payload is not None
        status = None
        for index, turn in enumerate(history):
            latest = index == len(history) - 1 and not busy
            slot = show_chat_turn(turn, index, context, latest=latest)
            if turn is decided:
                status = slot
        if payload is not None:
            # The card is resolved only once the API accepted the decision; until then it
            # stays retryable (an error, an expired proposal or an interrupted run).
            approved = payload["decision"]["approved"]
            if status is not None:
                status.caption("Sending your decision…")
            user_turn = {"role": "user", "content": "Approved" if approved else "Declined"}
            show_chat_turn(user_turn, len(history), context)
            errors: list[str] = []
            if run_turn(payload, history, context, user_turn, on_error=errors.append):
                decided.pop("decision_error", None)
                decided["resolution"] = "Approved." if approved else "Declined."
                if status is not None:
                    status.caption(decided["resolution"])
            else:
                decided["decision_error"] = (
                    errors[-1] if errors else "The assistant failed. Please try again."
                )
                st.rerun()  # show the card again with its buttons and the error
        elif message:
            user_turn = {"role": "user", "content": message}
            show_chat_turn(user_turn, len(history), context)
            payload = {"message": message, **chat_payload(history)}
            run_turn(payload, history, context, user_turn)


def session_panel() -> None:
    domain_view().session_panel()


def agent_home() -> None:
    copy = domain_copy()
    st.title(copy.title)
    st.caption(copy.intro)
    st.button("New conversation", type="tertiary", key="chat_reset_home", on_click=reset_chat)
    conversation, session = st.columns([0.68, 0.32], gap="large")
    with conversation, st.container(key="agent_conversation"):
        chat_conversation("home")
    with session:
        session_panel()


def chat_tab() -> None:
    st.title("Ask Curio")
    st.button("New chat", key="chat_reset_page", on_click=reset_chat)
    chat_conversation("page")


@st.fragment
def assistant_popup() -> None:
    with st.container(key="assistant_launcher"):
        popup = st.popover(
            "Ask Curio",
            icon=":material/chat_bubble:",
            type="primary",
            key="assistant_popup",
            on_change="rerun",
        )
    if not popup.open:
        return
    copy = domain_copy()
    with popup, st.container(key="assistant_panel"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.subheader("Curio", width="stretch", anchor=False)
            st.button("New chat", key="chat_reset_popup", type="tertiary", on_click=reset_chat)
            st.button("Close", key="chat_close", type="tertiary", on_click=close_assistant)
        st.caption(copy.popup_caption)
        chat_conversation("popup")
        st.caption(copy.popup_note)
