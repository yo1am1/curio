"""Applying approved effects: what the agent or a page saved joins the client-held
shortlist, reading list, saved drinks or memory."""

import streamlit as st

from curio.app.ui.books import READING_LIST_FULL, add_book, reading_list, remember_reading_tastes
from curio.app.ui.client import call_api
from curio.app.ui.cocktails import add_cocktail, cocktail_shortlist, remember_tastes
from curio.app.ui.common import MAX_SHORTLIST, PREFERENCE_LIMITS, merge_newest
from curio.app.ui.fashion import add_to_shortlist, preferences, product_name, shortlist


def saved_count(count: int, noun: str) -> str:
    return f"Saved {count} {noun if count == 1 else noun + 's'} to your shortlist."


def announce_shortlist(added: int, requested: int, noun: str, size: int) -> None:
    """Report what an applied effect really added: duplicates and a full list add nothing."""
    if added:
        st.toast(saved_count(added, noun), icon=":material/favorite:")
    if added < requested and size >= MAX_SHORTLIST:
        st.toast(f"Your shortlist holds up to {MAX_SHORTLIST} {noun}s.", icon=":material/info:")


def announce_reading_list(added: int, requested: int, size: int) -> None:
    if added:
        noun = "book" if added == 1 else "books"
        st.toast(f"Added {added} {noun} to your reading list.", icon=":material/bookmark:")
    if added < requested and size >= MAX_SHORTLIST:
        st.toast(READING_LIST_FULL, icon=":material/info:")


def announce_memory(dropped: list) -> None:
    st.toast("Curio will remember that for this session.", icon=":material/memory:")
    if dropped:
        st.toast(
            f"Memory is full, so Curio forgot older values: {', '.join(map(str, dropped))}.",
            icon=":material/info:",
        )


def apply_effects(reply: dict) -> None:
    products = {p["product_id"]: p for p in reply.get("products", [])}
    cocktails = {c["drink_id"]: c for c in reply.get("cocktails", [])}
    books = {b["book_id"]: b for b in reply.get("books", [])}
    for effect in reply.get("applied", []):
        kind = effect["kind"]
        if kind == "shortlist":
            ids = effect.get("product_ids", [])
            added = 0
            for pid in ids:
                product = products.get(pid) or call_api("GET", f"/products/{pid}")
                if product:
                    added += add_to_shortlist(product, announce=False)
            announce_shortlist(added, len(ids), "piece", len(shortlist()))
        elif kind == "cocktail_shortlist":
            ids = effect.get("drink_ids", [])
            added = 0
            for did in ids:
                cocktail = cocktails.get(did) or call_api("GET", f"/cocktails/{did}")
                if cocktail:
                    added += add_cocktail(cocktail, announce=False)
            announce_shortlist(added, len(ids), "drink", len(cocktail_shortlist()))
        elif kind == "preferences" and effect.get("preferences"):
            memory = preferences()
            dropped = []
            for field, limit in PREFERENCE_LIMITS.items():
                memory[field], lost = merge_newest(
                    memory.get(field, []), effect["preferences"].get(field, []), limit
                )
                dropped += lost
            if effect["preferences"].get("max_price") is not None:
                memory["max_price"] = effect["preferences"]["max_price"]
            announce_memory(dropped)
        elif kind == "cocktail_preferences" and effect.get("cocktail_preferences"):
            announce_memory(remember_tastes(effect["cocktail_preferences"]))
        elif kind == "book_shortlist":
            ids = effect.get("book_ids", [])
            added = 0
            for bid in ids:
                book = books.get(bid) or call_api("GET", f"/books/{bid}")
                if book:
                    added += add_book(book, announce=False)
            announce_reading_list(added, len(ids), len(reading_list()))
        elif kind == "book_preferences" and effect.get("book_preferences"):
            announce_memory(remember_reading_tastes(effect["book_preferences"]))
        elif kind == "evening":
            saved = []
            for pid in effect.get("product_ids", []):
                product = products.get(pid) or call_api("GET", f"/products/{pid}")
                if product and add_to_shortlist(product, announce=False):
                    saved.append(product_name(product))
            for bid in effect.get("book_ids", []):
                book = books.get(bid) or call_api("GET", f"/books/{bid}")
                if book and add_book(book, announce=False):
                    saved.append(book["title"])
            for did in effect.get("drink_ids", []):
                cocktail = cocktails.get(did) or call_api("GET", f"/cocktails/{did}")
                if cocktail and add_cocktail(cocktail, announce=False):
                    saved.append(cocktail["name"])
            if saved:
                st.toast(f"Saved the evening: {', '.join(saved)}.", icon=":material/favorite:")
            else:
                st.toast("Everything was already saved.", icon=":material/info:")
