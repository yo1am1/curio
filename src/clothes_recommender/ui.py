"""Curio fashion-discovery MVP: a thin Streamlit client over the HTTP API.

Run with: uv run streamlit run src/clothes_recommender/ui.py
"""

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import streamlit as st

from clothes_recommender.config import get_settings

GRID_COLUMNS = 3
QUERY_PHOTO_COLUMNS = 6
EXAMPLE_QUERIES = [
    "striped summer dress",
    "white sneakers",
    "chunky knit cardigan",
]
STRATEGY_NAMES = {
    "learned": "Personalized ranking",
    "repeat": "Buy again + best-sellers",
    "popularity": "Recent best-sellers",
    "popularity_novel": "Best-sellers, new items only",
    "content": "Visual style (CLIP)",
    "collaborative": "Collaborative",
    "graph": "Purchase connections",
}


@st.cache_resource
def api_client() -> httpx.Client:
    # CLIP inference on CPU can take a few seconds on a cold start.
    return httpx.Client(base_url=get_settings().api_url, timeout=120.0)


def call_api(method: str, path: str, missing_ok: bool = False, **kwargs) -> dict | list | None:
    """Call the API; on failure show the server's explanation instead of a stack trace.

    With missing_ok, a 404 is an expected empty state and its explanation is shown as info.
    """
    try:
        response = api_client().request(method, path, **kwargs)
    except httpx.HTTPError:
        st.error("The catalog service is unavailable. Please try again.")
        return None
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:  # e.g. a plain-text 500 from a crashed worker
            detail = response.text
        (st.info if missing_ok and response.status_code == 404 else st.error)(detail)
        return None
    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


@st.cache_data(ttl=30, max_entries=2000)
def product_photo(product_id: int) -> bytes | None:
    # Fetched server-side rather than by URL: in Docker the browser can't resolve the API host.
    try:
        response = api_client().get(f"/products/{product_id}/image")
    except httpx.HTTPError:
        return None
    return response.content if response.is_success else None


@st.cache_data(max_entries=100)
def query_photo(photo_id: int) -> bytes | None:
    try:
        response = api_client().get(f"/search/examples/{photo_id}/image")
    except httpx.HTTPError:
        return None
    return response.content if response.is_success else None


def recommendation_reason(reasons: list[str]) -> str | None:
    """Summarize the strongest supplied evidence; keep raw evidence in the expander."""
    if reasons and reasons[0].startswith("Buy again:"):
        return reasons[0]
    if reasons and (" who bought " in reasons[0] or reasons[0].startswith("Popular in")):
        return reasons[0]  # collaborative: the purchase that points here most strongly
    for reason in reasons:
        if reason.startswith("Bought together with"):
            return re.sub(r"\s*\(product \d+\)|\s*\(lift [^)]+\)", "", reason)
    if any(reason.startswith("Bought on the same day") for reason in reasons):
        return "Bought together by the same shoppers."
    if any(reason.startswith("Same specific Shopify category") for reason in reasons):
        return "Same category, type and section, at a higher approximate price."
    if any(reason.startswith("Shares Shopify category") for reason in reasons):
        return "Shares a product category with a previous purchase."
    if any(reason.startswith("Shares ") for reason in reasons):
        return "Connected through shared catalog attributes."
    if any(reason.startswith("Popularity fallback") for reason in reasons):
        return "Popular in recent purchases; no personal graph connections were found."
    return None


def show_products(
    products: list[dict],
    scores: list[float] | None = None,
    reasons: list[list[str]] | None = None,
    *,
    context: str = "products",
) -> None:
    for start in range(0, len(products), GRID_COLUMNS):
        columns = st.columns(GRID_COLUMNS)
        for offset, product in enumerate(products[start : start + GRID_COLUMNS]):
            i = start + offset
            with (
                columns[offset],
                st.container(
                    key=f"product_card_{context}_{product['product_id']}_{i}", height="stretch"
                ),
            ):
                if (photo := product_photo(product["product_id"])) is not None:
                    st.image(photo, width="stretch")
                else:
                    with st.container(key=f"missing_photo_{context}_{product['product_id']}_{i}"):
                        st.caption("No photo available.", text_alignment="center")
                with st.container(
                    key=f"product_info_{context}_{product['product_id']}_{i}", gap=None
                ):
                    st.text(product["name"])
                    st.markdown(f"**€{product['price']:.2f}**")
                    st.caption(f"{product['product_type']} · {product['colour']}")
                evidence = reasons[i] if reasons is not None else []
                if reason := recommendation_reason(evidence):
                    st.caption(reason)
                st.space("stretch")
                if scores is not None or evidence:
                    with st.expander("Recommendation details"):
                        if scores is not None:
                            st.caption(f"Ranking score: {scores[i]:.3f}")
                        for detail in dict.fromkeys(evidence):
                            st.text(detail)
                if st.button(
                    f"View {product['name']}",
                    key=f"detail_{context}_{product['product_id']}_{i}",
                    width="stretch",
                ):
                    product_details(product["product_id"])


def show_hits(hits: list[dict] | None, *, context: str = "results") -> None:
    if hits:
        show_products(
            [h["product"] for h in hits],
            [h["score"] for h in hits],
            [h.get("reasons", []) for h in hits],
            context=context,
        )
    elif hits is not None:
        st.info("No matching products.")


def customer_selector(label: str, key: str) -> dict | None:
    """Start with a few explained examples; fetch the full ID list only on request."""
    examples = call_api("GET", "/demo/customers") or []
    examples_by_id = {example["user_id"]: example for example in examples[:10]}
    saved_mode = f"saved_{key}_browse_all"
    selected = st.session_state.get("selected_customer")
    default_all = st.session_state.get(saved_mode, False) or (
        selected is not None and selected not in examples_by_id
    )
    browse_all = st.toggle("Browse all customers", value=default_all, key=f"{key}_browse_all")
    st.session_state[saved_mode] = browse_all
    user_ids = call_api("GET", "/users") if browse_all else list(examples_by_id)
    if not user_ids:
        st.info(
            "No customers are available to explore."
            if browse_all
            else "No example customers are available. Browse all customers to explore."
        )
        return None
    user_id = st.selectbox(
        label,
        user_ids,
        index=user_ids.index(st.session_state.get("selected_customer"))
        if st.session_state.get("selected_customer") in user_ids
        else 0,
        key=f"{key}_{'all' if browse_all else 'examples'}",
        format_func=lambda uid: (
            str(uid) if browse_all else f"{examples_by_id[uid]['label']} (#{uid})"
        ),
    )
    st.session_state["selected_customer"] = user_id
    example = examples_by_id.get(user_id, {"user_id": user_id})
    if example.get("description"):
        st.caption(example["description"])
    return example


def recommendations_tab() -> None:
    left, right = st.columns([0.5, 0.5], gap="large", vertical_alignment="bottom")
    with left:
        st.title("For you")
    toolbar = right.container(key="feed_controls", horizontal=True, horizontal_alignment="right")
    with toolbar.popover("Shopping history"):
        example = customer_selector("User", "recommendation_user")
        if example is None:
            return
        user_id = example["user_id"]
        profile = call_api("GET", f"/users/{user_id}")
        if profile:
            purchases = profile["purchases"]
            st.caption(f"Shopping history #{user_id}")
            if purchases:
                with st.expander("Recent purchases (up to 10)"):
                    st.dataframe(
                        [
                            {
                                "Product": item["product"]["name"],
                                "Type": item["product"]["product_type"],
                                "Price (€)": item["product"]["price"],
                                "Date": item.get("purchase_date", ""),
                            }
                            for item in purchases[:10]
                        ],
                        hide_index=True,
                        width="stretch",
                    )
    strategies = ["repeat", "collaborative", "content", "graph"]
    if st.session_state.get("service_health", {}).get("ranking_enabled"):
        strategies.insert(0, "learned")
    with toolbar.popover("How picks are chosen"):
        strategy = st.radio(
            "Strategy",
            strategies,
            index=strategies.index(st.session_state.get("saved_strategy", strategies[0]))
            if st.session_state.get("saved_strategy", strategies[0]) in strategies
            else 0,
            format_func={
                "learned": "Personalized ranking",
                "repeat": "Buy again",
                "collaborative": "Similar shoppers",
                "content": "Visual style",
                "graph": "Purchase connections",
            }.get,
            horizontal=True,
            key="recommendation_strategy",
        )
        st.session_state["saved_strategy"] = strategy
        st.caption(
            "Buy again is a strong simple baseline. Personalized ranking learns from "
            "earlier purchases; discovery approaches deliberately show only new items."
        )
    descriptions = {
        "learned": "Favorites and new finds, ranked by your purchase patterns and recent demand.",
        "repeat": "Your most recent purchases first, followed by recent best-sellers.",
        "collaborative": "New pieces bought by shoppers with items like yours.",
        "content": "New pieces with photos close to the styles in your history.",
        "graph": "New pieces connected through purchases and catalog attributes.",
    }
    st.write(descriptions[strategy])
    with st.spinner("Finding your pieces…"):
        show_hits(
            call_api(
                "GET",
                f"/users/{user_id}/recommendations",
                params={"strategy": strategy, "top_k": 12},
            ),
            context="recommendations",
        )


@st.dialog("Product details", width="large")
def product_details(product_id: int) -> None:
    product = call_api("GET", f"/products/{product_id}")
    if product is None:
        return
    with st.container(key="product_detail_body"):
        left, right = st.columns([0.45, 0.55], gap="large")
        with left:
            if (photo := product_photo(product_id)) is not None:
                st.image(photo, width="stretch")
            else:
                st.info("A photo is not available for this item.")
        with right:
            st.subheader(product["name"])
            st.markdown(f"**€{product['price']:.2f}**")
            st.caption(
                f"{product['product_type']} · {product['colour']} · {product.get('section', '')}"
            )
            st.text(product.get("description") or "No further description is available.")
            st.caption(
                "H&M research catalog, 2020. Prices are approximate; current stock is unknown."
            )
            with st.container(horizontal=True):
                saved = any(p["product_id"] == product_id for p in shortlist())
                st.button(
                    "Saved" if saved else "Save to shortlist",
                    icon=":material/favorite:" if saved else ":material/favorite_border:",
                    key=f"detail_save_{product_id}",
                    disabled=saved,
                    on_click=add_to_shortlist,
                    args=(product,),
                )
                if st.button(
                    "Ask Curio what goes with it",
                    icon=":material/chat_bubble:",
                    key=f"detail_ask_{product_id}",
                ):
                    # Dialogs rerun on their own; a full rerun closes this one and opens chat.
                    queue_message(
                        f"What goes with the {product['name']} (product {product_id})?",
                        True,
                        domain=FASHION,
                    )
                    st.rerun()
    related = st.radio(
        "Explore this piece",
        ["similar", "complement"],
        horizontal=True,
        format_func={"similar": "Similar styles", "complement": "Bought together"}.get,
        key=f"detail_relation_{product_id}",
    )
    path = (
        f"/products/{product_id}/similar"
        if related == "similar"
        else f"/products/{product_id}/related"
    )
    params = {"top_k": 3} if related == "similar" else {"top_k": 3, "kind": "complement"}
    hits = call_api("GET", path, params=params, missing_ok=True)
    if hits:
        # Related-item buttons cannot open a second native dialog inside this one.
        for hit in hits:
            item = hit["product"]
            st.text(f"{item['name']} · {item['colour']} · €{item['price']:.2f}")
    elif hits is not None:
        st.info("No related pieces are available yet.")


def graph_view(
    graph: dict, product_id: int, theme_type: str | None = None
) -> tuple[dict, list[dict]]:
    """A bounded one-hop view, with stable positions and the same edges as a text table."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    focus = next((node for node in nodes.values() if node["product_id"] == product_id), None)
    edges = sorted(
        (edge for edge in graph["edges"] if edge["source"] in nodes and edge["target"] in nodes),
        key=lambda edge: (-edge["weight"], edge["source"], edge["target"], edge["relation"]),
    )
    visible = {}
    if focus is not None:
        visible[focus["id"]] = focus
        for edge in edges:
            if focus["id"] in (edge["source"], edge["target"]):
                neighbor = edge["target"] if edge["source"] == focus["id"] else edge["source"]
                if len(visible) < 12:
                    visible[neighbor] = nodes[neighbor]
    ordered = sorted(visible.values(), key=lambda node: (node["kind"], node["id"]))
    neighbors = [node for node in ordered if node is not focus]
    positions = {focus["id"]: (0.0, 0.0)} if focus else {}
    for i, node in enumerate(neighbors):
        angle = 2 * math.pi * i / len(neighbors)
        positions[node["id"]] = (math.cos(angle), math.sin(angle))
    points = [
        {
            "id": node["id"],
            "Label": node["label"],
            "Kind": node["kind"].replace("_", " ").title(),
            "Product ID": node["product_id"],
            "x": positions[node["id"]][0],
            "y": positions[node["id"]][1],
            "short_label": node["label"][:22] + ("…" if len(node["label"]) > 22 else ""),
            "focused": node is focus,
        }
        for node in ordered
    ]
    links = []
    table = []
    for edge in edges:
        if edge["source"] not in visible or edge["target"] not in visible:
            continue
        if len(links) == 24:
            break
        source, target = visible[edge["source"]], visible[edge["target"]]
        links.append(
            {
                **edge,
                "x": positions[source["id"]][0],
                "y": positions[source["id"]][1],
                "x2": positions[target["id"]][0],
                "y2": positions[target["id"]][1],
                "Source": source["label"],
                "Target": target["label"],
            }
        )
        table.append(
            {
                "Source": source["label"],
                "Source type": source["kind"],
                "Relationship": edge["relation"],
                "Target": target["label"],
                "Target type": target["kind"],
                "Weight": edge["weight"],
            }
        )
    dark = theme_type == "dark"
    label_color = "#FAFAFA" if dark else "#31333F"
    colors = (
        {"product": "#55B7CA", "attribute": "#E0AC58", "taxonomy": "#B699E7"}
        if dark
        else {"product": "#287D8E", "attribute": "#A66A16", "taxonomy": "#7254A3"}
    )
    kinds = sorted({node["kind"] for node in ordered})
    xy = {
        axis: {
            "field": axis,
            "type": "quantitative",
            "axis": None,
            "scale": {"domain": [-1.45, 1.45]},
        }
        for axis in ("x", "y")
    }
    spec = {
        "description": (
            "Product neighborhood. Node types appear in the legend; "
            "full relationships are listed in the table below."
        ),
        "height": 360,
        "layer": [
            {
                "data": {"values": links},
                "mark": {
                    "type": "rule",
                    "color": "#9CA3AF" if dark else "#64748B",
                    "opacity": 0.75,
                },
                "encoding": {
                    **xy,
                    "x2": {"field": "x2"},
                    "y2": {"field": "y2"},
                    "tooltip": [
                        {"field": field, "type": "nominal"}
                        for field in ("Source", "relation", "Target", "weight")
                    ],
                },
            },
            {
                "data": {"values": points},
                "mark": {"type": "circle", "opacity": 1},
                "params": [
                    {"name": "node", "select": {"type": "point", "fields": ["id"], "toggle": False}}
                ],
                "encoding": {
                    **xy,
                    "size": {"condition": {"test": "datum.focused", "value": 350}, "value": 180},
                    "color": {
                        "field": "Kind",
                        "type": "nominal",
                        "scale": {
                            "domain": [kind.replace("_", " ").title() for kind in kinds],
                            "range": [colors.get(kind, "#64748B") for kind in kinds],
                        },
                        "legend": {"title": "Node type", "orient": "bottom", "columns": 3},
                    },
                    "tooltip": [
                        {"field": field, "type": "nominal"}
                        for field in ("Label", "Kind", "Product ID", "id")
                    ],
                },
            },
            {
                "data": {"values": points},
                "mark": {"type": "text", "dy": -19, "fontSize": 12, "color": label_color},
                "encoding": {**xy, "text": {"field": "short_label", "type": "nominal"}},
            },
        ],
        "config": {
            "view": {"stroke": None},
            "legend": {
                "labelColor": label_color,
                "titleColor": label_color,
                "labelFontSize": 12,
                "titleFontSize": 12,
            },
        },
    }
    return spec, table


def knowledge_graph_tab() -> None:
    left, right = st.columns([0.65, 0.35], vertical_alignment="bottom")
    with left:
        st.title("Product connections")
    with right, st.popover("Shopping history", width="stretch"):
        example = customer_selector("Customer", "graph_user")
    if example is None:
        return
    user_id = example["user_id"]
    with st.spinner("Loading purchases and graph recommendations…"):
        profile = call_api("GET", f"/users/{user_id}")
        hits = call_api("GET", f"/users/{user_id}/recommendations", params={"strategy": "graph"})
    purchases = profile["purchases"] if profile else []
    products = {purchase["product"]["product_id"]: purchase["product"] for purchase in purchases}
    purchased_ids = set(products)
    recommended = {hit["product"]["product_id"]: hit for hit in hits or []}
    products.update(
        {pid: hit["product"] for pid, hit in recommended.items() if pid not in products}
    )
    if not products:
        st.info("No purchases or graph recommendations are available for this customer.")
        return
    product_ids = list(products)
    focus_id = example.get("focus_product_id")
    with st.container(key="connection_controls", border=True):
        st.subheader("Choose a starting piece")
        product_id = st.selectbox(
            "Focus product",
            product_ids,
            index=product_ids.index(focus_id) if focus_id in products else 0,
            key=f"graph_focus_{user_id}_{focus_id}",
            format_func=lambda pid: (
                f"{'Purchased' if pid in purchased_ids else 'Recommended'} · "
                f"{products[pid]['name']} (#{pid})"
            ),
        )
        product = products[product_id]
        st.caption(
            f"Shopping history #{user_id} · {product['product_type']} · €{product['price']:.2f}"
        )
        for reason in recommended.get(product_id, {}).get("reasons", []):
            st.caption(reason)
    st.subheader("Explore the connections")
    st.write("Select a product node to explore its related pieces.")
    with st.spinner("Loading product neighborhood…"):
        graph = call_api("GET", f"/products/{product_id}/graph", params={"top_k": 12})
    selected_id = None
    selected_product_id = product_id
    selected_label = product["name"]
    if graph is not None:
        spec, table = graph_view(graph, product_id, st.context.theme.type)
        if not table:
            st.info("No relationships are available for this product.")
        else:
            event = st.vega_lite_chart(
                spec,
                width="stretch",
                key=f"graph_chart_{user_id}_{product_id}",
                on_select="rerun",
                selection_mode="node",
            )
            selection = event.get("selection", {}).get("node", [])
            if selection:
                selected_id = selection[0].get("id")
            selected = next((node for node in graph["nodes"] if node["id"] == selected_id), None)
            if selected:
                st.write(f"Selected node: {selected['label']} ({selected['kind']})")
                if selected["product_id"] is not None:
                    selected_product_id = selected["product_id"]
                    selected_label = selected["label"]
                edges = spec["layer"][0]["data"]["values"]
                table = [
                    row
                    for row, edge in zip(table, edges, strict=True)
                    if selected_id in (edge["source"], edge["target"])
                ]
            st.caption("Node spacing does not measure similarity.")
            with st.expander("Relationships"):
                st.caption("This view shows up to 12 nodes and 24 relationships.")
                st.dataframe(table, hide_index=True, width="stretch")
    st.subheader(f"Related products · {selected_label}")
    kind = st.radio(
        "Relationship",
        ["complement", "alternative", "similar"],
        horizontal=True,
        key="graph_related_kind",
        format_func={
            "complement": "Bought together",
            "alternative": "Higher-price alternatives",
            "similar": "Looks similar",
        }.get,
    )
    if kind == "complement":
        st.caption("Co-purchased products from different product groups.")
    elif kind == "similar":
        st.caption(
            "Nearest product photos in the CLIP image index. No purchase data is used, "
            "so this works for products nobody has bought together yet."
        )
        with st.spinner("Finding look-alikes…"):
            show_hits(
                call_api("GET", f"/products/{selected_product_id}/similar"), context="graph_similar"
            )
        return
    else:
        st.caption(
            "Same specific Shopify category, product type and section, "
            "at a higher approximate price. "
            "Unclassified or broad categories are excluded. "
            "Price does not indicate quality or proven upsell value."
        )
    with st.spinner("Loading related products…"):
        related = call_api("GET", f"/products/{selected_product_id}/related", params={"kind": kind})
    if related == []:
        st.info(
            "No higher-price alternatives with a matching specific Shopify category, product type "
            "and section. Unclassified or broad categories are excluded."
            if kind == "alternative"
            else "No co-purchased products from different product groups."
        )
    else:
        show_hits(related, context="graph_related")


def use_example_query(query: str) -> None:
    st.session_state["text_query"] = query


def text_search_tab() -> None:
    with st.container(key="text_search_panel", border=True):
        st.subheader("Describe your piece")
        with st.form("description_search", border=False):
            left, middle, right = st.columns([0.6, 0.23, 0.17], vertical_alignment="bottom")
            with left:
                query = st.text_input(
                    "Describe what you are looking for",
                    key="text_query",
                    placeholder="e.g. a chunky cream cardigan",
                )
            with middle:
                sections = call_api("GET", "/sections") or []
                section = st.selectbox(
                    "Section", [None, *sections], format_func=lambda s: s or "All"
                )
            with right:
                st.form_submit_button(
                    "Search photos",
                    type="primary",
                    key="description_search_submit",
                    width="stretch",
                )
            st.caption("Or try an example")
            with st.container(key="text_example", horizontal=True):
                for index, example in enumerate(EXAMPLE_QUERIES):
                    st.form_submit_button(
                        example,
                        key=f"text_example_{index}",
                        on_click=use_example_query,
                        args=(example,),
                    )
    if query.strip():
        with st.container(key="text_search_results"):
            st.subheader("Matching pieces")
            with st.spinner("Searching photos…"):
                show_hits(
                    call_api(
                        "POST",
                        "/search/text",
                        json={"query": query, "top_k": 9, "section": section},
                    )
                )


def choose_query_photo(photo_id: int) -> None:
    st.session_state["query_photo_id"] = photo_id
    # A new uploader key clears the old file without writing read-only widget state.
    st.session_state["photo_upload_version"] = st.session_state.get("photo_upload_version", 0) + 1


def choose_uploaded_photo() -> None:
    st.session_state.pop("query_photo_id", None)


def image_search_tab() -> None:
    with st.container(key="photo_search_panel", border=True):
        st.subheader("Start with a photo")
        upload = st.file_uploader(
            "Upload a photo of a garment",
            type=["png", "jpg", "jpeg"],
            max_upload_size=10,
            key=f"photo_upload_{st.session_state.get('photo_upload_version', 0)}",
            on_change=choose_uploaded_photo,
        )
        examples = call_api("GET", "/search/examples") or []
        if examples:
            with st.expander("Or pick an example photo"):
                st.caption(
                    "Examples are garments outside this catalog. Results show similar products."
                )
                for start in range(0, len(examples), QUERY_PHOTO_COLUMNS):
                    columns = st.columns(QUERY_PHOTO_COLUMNS)
                    for column, example in zip(
                        columns, examples[start : start + QUERY_PHOTO_COLUMNS], strict=False
                    ):
                        with column:
                            if (photo := query_photo(example["photo_id"])) is not None:
                                st.image(photo, width="stretch")
                            st.button(
                                example["product_type"],
                                key=f"query_photo_{example['photo_id']}",
                                help=f"{example['name']} · {example['colour']}",
                                on_click=choose_query_photo,
                                args=(example["photo_id"],),
                                width="stretch",
                            )

    if upload is not None:
        name, content = upload.name, upload.getvalue()
    elif (photo_id := st.session_state.get("query_photo_id")) is not None:
        name, content = f"{photo_id}.jpg", query_photo(photo_id)
    else:
        return
    if content is None:
        st.error("The example photo is no longer available.")
        return
    with st.container(key="photo_search_results"):
        # The API validates file size and pixels before the UI tries to decode a preview.
        with st.spinner("Searching photos…"):
            hits = call_api(
                "POST",
                "/search/image",
                params={"top_k": 9},
                files={"file": (name, content)},
            )
        if hits is None:
            return
        left, right = st.columns([0.2, 0.8], gap="large")
        with left:
            st.subheader("Query photo")
            st.image(content, width="stretch")
        with right:
            st.subheader("Visually similar pieces")
            show_hits(hits)


def evaluation_chart_spec(rows: list[dict], baseline: float | None) -> dict:
    """Full-holdout MAP bars; the rule marks the strong repeat baseline when available."""
    values = [
        {"Strategy": row["Strategy"], "MAP@12": row["MAP@12"], "Label": f"{row['MAP@12']:.4f}"}
        for row in rows
        if row["MAP@12"] is not None
    ]
    y = {
        "field": "Strategy",
        "type": "nominal",
        "sort": "-x",
        "title": None,
        "axis": {"labelLimit": 220, "ticks": False, "domain": False},
    }
    x = {
        "field": "MAP@12",
        "type": "quantitative",
        "title": "MAP@12 · all held-out purchases",
        "scale": {"zero": True},
        "axis": {"format": ".3f", "tickCount": 5},
    }
    layers = [
        {
            "mark": {"type": "bar", "color": "#9c4935", "cornerRadiusEnd": 2},
            "encoding": {
                "y": y,
                "x": x,
                "tooltip": [{"field": "Strategy"}, {"field": "MAP@12", "format": ".4f"}],
            },
        },
        {
            "mark": {"type": "text", "align": "left", "dx": 6},
            "encoding": {"y": y, "x": x, "text": {"field": "Label"}},
        },
    ]
    if baseline is not None:
        layers.append(
            {
                "data": {"values": [{"baseline": baseline}]},
                "mark": {"type": "rule", "strokeDash": [4, 4], "opacity": 0.7},
                "encoding": {"x": {"field": "baseline", "type": "quantitative"}},
            }
        )
    return {
        "description": "Full-holdout MAP@12 with the baseline as a dashed rule.",
        "data": {"values": values},
        "height": 44 * len(values),
        "layer": layers,
        "config": {"view": {"stroke": None}},
    }


def evaluation_tab() -> None:
    st.title("Model quality")
    st.caption("Measured against what people bought next.")
    evaluation = call_api("GET", "/evaluation", missing_ok=True)
    if evaluation is None:
        return
    scope = evaluation["sample_scope"]
    if evaluation.get("evaluation_role") == "validation":
        st.caption("Earlier validation week; not the final holdout.")
    st.markdown(
        f"**{evaluation['users_evaluated']:,} customers** · "
        f"{evaluation['cutoff']} to {evaluation['holdout_end']} · "
        f"{evaluation['top_k']} recommendations each"
    )
    st.caption(
        "Prepared catalog and customer selection may favor every strategy; "
        "these are sample-level results, not population or leaderboard estimates."
    )
    results = evaluation["results"]
    if not results:
        st.info("No evaluated strategies are available yet.")
        return
    winner = max(results, key=lambda name: results[name]["full_holdout"]["map_at_12"] or 0)
    best = results[winner]["full_holdout"]
    st.subheader(STRATEGY_NAMES.get(winner, winner))
    st.caption("Highest full-holdout MAP in this measured sample.")
    columns = st.columns(3)
    columns[0].metric(
        "Customers reached",
        f"{best['hit_rate']:.1%}" if best.get("hit_rate") is not None else "Not recorded",
    )
    columns[1].metric(
        "Purchased items recovered",
        f"{best['recall_at_12']:.1%}" if best.get("recall_at_12") is not None else "Not recorded",
    )
    columns[2].metric("Ranking quality · MAP@12", f"{best['map_at_12']:.4f}")
    if gain := evaluation.get("learned_vs_repeat"):
        statement = (
            "The interval includes zero; the single-week advantage remains uncertain."
            if (gain["lower_95"] <= 0 <= gain["upper_95"])
            else "Interval covers selected-customer uncertainty only."
        )
        st.caption(
            f"Observed MAP gain vs buy-again: {gain['mean_delta']:+.4f}. "
            f"95% paired-customer interval: {gain['lower_95']:+.4f} to {gain['upper_95']:+.4f}. "
            + statement
        )
    baseline_name = "repeat" if "repeat" in results else "popularity"
    baseline = results.get(baseline_name, {}).get("full_holdout", {}).get("map_at_12")
    rows = [
        {
            "Strategy": STRATEGY_NAMES.get(name, name),
            "MAP@12": values["full_holdout"]["map_at_12"],
            "Hit rate": values["full_holdout"].get("hit_rate"),
            "Recall@12": values["full_holdout"].get("recall_at_12"),
            "vs baseline": values["full_holdout"]["map_at_12"] / baseline if baseline else None,
            "Catalog coverage": values["catalog_coverage"],
            "List fill": values["recommendation_fill"],
        }
        for name, values in results.items()
    ]
    rows.sort(key=lambda row: -(row["MAP@12"] or 0))
    st.vega_lite_chart(evaluation_chart_spec(rows, baseline), width="stretch")
    st.caption(
        f"Dashed rule: {STRATEGY_NAMES.get(baseline_name, baseline_name)}. "
        "Repeat purchases are included for every strategy."
    )
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "MAP@12": st.column_config.NumberColumn(format="%.4f"),
            "Hit rate": st.column_config.NumberColumn(format="percent"),
            "Recall@12": st.column_config.NumberColumn(format="percent"),
            "vs baseline": st.column_config.NumberColumn(format="%.2fx"),
            "Catalog coverage": st.column_config.NumberColumn(format="percent"),
            "List fill": st.column_config.NumberColumn(format="percent"),
        },
    )
    st.caption(
        "Hit rate: customers with at least one matching recommendation. "
        "Recall: average share of each customer's purchased items recovered. "
        "MAP@12 measures ranking quality, not a probability of buying."
    )
    index = evaluation.get("content_index")
    if index is None:
        st.caption("Content-based results are missing: the evaluation ran without an image index.")
    elif index["indexed_products"] < scope["catalog_products"]:
        st.caption(
            f"Visual-style results use {index['indexed_products']:,} of "
            f"{scope['catalog_products']:,} product photos. This partial index limits coverage."
        )
    with st.expander("Data, protocol and limits"):
        st.write(
            f"The {scope['prepared_users']:,}-customer sample contains "
            f"{scope['catalog_products']:,} products. Every purchase-derived signal is trained "
            "strictly before the holdout. Cohort and catalog selection may use the full window; "
            "results describe this sample, not the population or a competition leaderboard."
        )
        st.write(
            "Novel-item metrics use the original ranked lists and are diagnostic. "
            "A repeat strategy and a discovery strategy solve different tasks."
        )
        st.json(evaluation.get("repeat_purchase_limitation", {}))


def discovery_page() -> None:
    st.title("Discover")
    mode = st.segmented_control(
        "Search with",
        ["Description", "Photo"],
        default="Description",
        required=True,
        key="discovery_mode",
        label_visibility="collapsed",
    )
    if mode == "Description":
        text_search_tab()
    else:
        image_search_tab()


# --- Domains -------------------------------------------------------------------------------

FASHION = "fashion"
COCKTAILS = "cocktails"
DOMAIN_LABELS = {
    FASHION: ":material/checkroom: Fashion",
    COCKTAILS: ":material/local_bar: Cocktails",
}
# Client-held state per domain; the fashion keys predate the cocktail domain and stay as they are.
STATE_KEYS = {
    FASHION: {
        "history": "chat_history",
        "shortlist": "shortlist",
        "memory": "preferences",
        "queued": "chat_queued",
        "decision": "chat_decision",
    },
    COCKTAILS: {
        "history": "chat_history_cocktails",
        "shortlist": "cocktail_shortlist",
        "memory": "cocktail_preferences",
        "queued": "chat_queued_cocktails",
        "decision": "chat_decision_cocktails",
    },
}
# Pages that belong to one domain; a deep link to one of them selects that domain.
PAGE_DOMAINS = {
    "fashion": FASHION,
    "discover": FASHION,
    "connections": FASHION,
    "quality": FASHION,
    "cocktails": COCKTAILS,
}
COCKTAILS_UNAVAILABLE = (
    "Cocktail recipes are not prepared on this server yet (run scripts.prepare_cocktails)."
)
RECIPE_CREDIT = "Recipe data: TheCocktailDB · Please drink responsibly."


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


DOMAIN_COPY = {
    FASHION: DomainCopy(
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
        starters=("White sneakers under €40", "A linen shirt for summer", "Warm knitwear"),
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
    ),
    COCKTAILS: DomainCopy(
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
    ),
}


def current_domain() -> str:
    domain = st.session_state.get("domain", FASHION)
    return domain if domain in STATE_KEYS else FASHION


def state_key(name: str, domain: str | None = None) -> str:
    return STATE_KEYS[domain or current_domain()][name]


def chat_history(domain: str | None = None) -> list[dict]:
    return st.session_state.setdefault(state_key("history", domain), [])


# --- Agent conversation -------------------------------------------------------------------

MAX_SHORTLIST = 20
MAX_TASTES = 10
STEP_LABELS = {
    "Understand request": "Plan",
    "SearchCatalog": "Search catalog",
    "InspectProducts": "Inspect products",
    "FindRelated": "Find related pieces",
    "PersonalPicks": "Personal picks",
    "SearchCocktails": "Search recipes",
    "InspectCocktails": "Inspect recipes",
    "SimilarCocktails": "Find similar drinks",
    "WhatCanIMake": "Check your ingredients",
    "AskShopper": "Question for you",
    "SaveToShortlist": "Shortlist proposal",
    "RememberPreferences": "Memory proposal",
    "SaveCocktails": "Shortlist proposal",
    "RememberTastes": "Memory proposal",
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
GROUNDING_BADGES = {
    FASHION: {
        "verified": ("Prices verified against the catalog", "green", ":material/verified:"),
        "repaired": ("Self-corrected to match the catalog", "orange", ":material/auto_fix_high:"),
        "fallback": ("Unverified answer replaced with catalog facts", "red", ":material/shield:"),
    },
    COCKTAILS: {
        "verified": ("Checked against the recipes", "green", ":material/verified:"),
        "repaired": ("Self-corrected to match the recipes", "orange", ":material/auto_fix_high:"),
        "fallback": ("Unverified answer replaced with recipe facts", "red", ":material/shield:"),
    },
}
# Approval tools by policy setting, per domain.
APPROVAL_TOOLS = {
    FASHION: {"ask_shortlist": "SaveToShortlist", "ask_memory": "RememberPreferences"},
    COCKTAILS: {"ask_shortlist": "SaveCocktails", "ask_memory": "RememberTastes"},
}
# Proposal arguments the shopper may narrow: list values can be dropped one by one.
APPROVAL_LIST_FIELDS = (
    "product_ids",
    "sections",
    "colours",
    "drink_ids",
    "liked_ingredients",
    "avoided_ingredients",
)
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|~$])")


def plain(text: str) -> str:
    """Show model or catalog text literally inside Streamlit markdown."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


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


def shortlist() -> list[dict]:
    return st.session_state.setdefault("shortlist", [])


def add_to_shortlist(product: dict, announce: bool = True) -> None:
    items = shortlist()
    if any(item["product_id"] == product["product_id"] for item in items):
        return
    if len(items) >= MAX_SHORTLIST:
        st.toast(f"Your shortlist holds up to {MAX_SHORTLIST} pieces.", icon=":material/info:")
        return
    items.append(product)
    if announce:
        st.toast(f"Saved {product['name']} to your shortlist.", icon=":material/favorite:")


def remove_from_shortlist(product_id: int) -> None:
    st.session_state["shortlist"] = [p for p in shortlist() if p["product_id"] != product_id]


def preferences() -> dict:
    return st.session_state.setdefault("preferences", {})


def forget_preference(field: str, value: object) -> None:
    memory = preferences()
    if field == "max_price":
        memory.pop("max_price", None)
    else:
        memory[field] = [item for item in memory.get(field, []) if item != value]


def cocktail_shortlist() -> list[dict]:
    return st.session_state.setdefault("cocktail_shortlist", [])


def add_cocktail(cocktail: dict, announce: bool = True) -> None:
    items = cocktail_shortlist()
    if any(item["drink_id"] == cocktail["drink_id"] for item in items):
        return
    if len(items) >= MAX_SHORTLIST:
        st.toast(f"Your shortlist holds up to {MAX_SHORTLIST} drinks.", icon=":material/info:")
        return
    items.append(cocktail)
    if announce:
        st.toast(f"Saved {cocktail['name']} to your shortlist.", icon=":material/favorite:")


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


def remember_tastes(tastes: dict) -> None:
    memory = cocktail_memory()
    if tastes.get("alcohol_free"):
        memory["alcohol_free"] = True
    for field in ("liked_ingredients", "avoided_ingredients"):
        values = [*memory.get(field, []), *tastes.get(field, [])]
        memory[field] = list(dict.fromkeys(values))[:MAX_TASTES]


def auto_approve(domain: str | None = None) -> list[str]:
    policy = st.session_state.get("approval_policy", {})
    return [
        tool
        for setting, tool in APPROVAL_TOOLS[domain or current_domain()].items()
        if not policy.get(setting, True)
    ]


def queue_message(text: str, open_popup: bool = False, domain: str | None = None) -> None:
    st.session_state[state_key("queued", domain)] = text
    if open_popup:
        st.session_state["assistant_popup"] = True


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
    prefix = "feedback" if current_domain() == FASHION else "feedback_cocktails"
    return f"{prefix}_{context}_{index}"


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


def show_chat_products(products: list[dict], index: int, context: str) -> None:
    saved = {item["product_id"] for item in shortlist()}
    compact = context == "popup"
    columns = None if compact else st.columns(min(5, max(3, len(products))))
    for position, product in enumerate(products):
        pid = product["product_id"]
        slot = st.container() if compact else columns[position % len(columns)]
        with (
            slot,
            st.container(
                key=f"chat_product_{context}_{index}_{pid}",
                horizontal=compact,
                vertical_alignment="center" if compact else "top",
            ),
        ):
            if (photo := product_photo(pid)) is not None:
                st.image(photo, width=56 if compact else "stretch")
            with st.container(gap=None):
                st.text(product["name"])
                st.caption(f"€{product['price']:.2f} · {product['colour']}")
            with st.container(horizontal=True, gap="small"):
                if st.button("View", key=f"chat_view_{context}_{index}_{pid}"):
                    product_details(pid)
                st.button(
                    "Saved" if pid in saved else "Save",
                    key=f"chat_save_{context}_{index}_{pid}",
                    icon=":material/favorite:" if pid in saved else ":material/favorite_border:",
                    type="tertiary",
                    disabled=pid in saved,
                    on_click=add_to_shortlist,
                    args=(product,),
                )


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


def approval_options(action: dict) -> list[tuple[str, object, str]]:
    """Checkbox rows (field, value, label) for every value the shopper may drop."""
    args = action["args"]
    products = {p["product_id"]: p for p in action.get("products") or []}
    cocktails = {c["drink_id"]: c for c in action.get("cocktails") or []}
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
            elif field == "liked_ingredients":
                label = f"Likes {value}"
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


def approval_card(turn: dict, index: int, context: str, active: bool) -> None:
    action = turn["pending_action"]
    with st.container(key=f"approval_{context}_{index}", border=True):
        st.markdown(":material/how_to_reg: **Needs your approval**")
        st.caption(plain(action.get("summary", "")))
        if not active:
            st.caption(turn.get("resolution", "No longer pending."))
            return
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


def show_chat_turn(turn: dict, index: int, context: str, latest: bool = False) -> None:
    with st.chat_message(turn["role"]):
        if turn["role"] == "assistant":
            show_trace(turn.get("steps", []))
        st.markdown(turn["content"])
        grounding = (turn.get("grounding") or {}).get("status")
        badges = GROUNDING_BADGES[current_domain()]
        if grounding in badges:
            label, color, icon = badges[grounding]
            st.badge(label, color=color, icon=icon)
        if turn.get("products"):
            show_chat_products(turn["products"], index, context)
        if turn.get("cocktails"):
            show_chat_cocktails(turn["cocktails"], index, context)
        if turn.get("pending_action"):
            approval_card(turn, index, context, active=latest and not turn.get("resolution"))
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


def reset_chat() -> None:
    st.session_state[state_key("history")] = []
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
    return item


def fashion_context() -> dict:
    context: dict = {}
    memory = {key: value for key, value in preferences().items() if value}
    if memory:
        context["preferences"] = memory
    if st.session_state.get("chat_shopper"):
        context["shopper_id"] = st.session_state["chat_shopper"]
    if shortlist():
        context["shortlist"] = [p["product_id"] for p in shortlist()]
    return context


def cocktail_context() -> dict:
    context: dict = {"domain": COCKTAILS}
    memory = {key: value for key, value in cocktail_memory().items() if value}
    if memory:
        context["cocktail_preferences"] = memory
    if cocktail_shortlist():
        context["cocktail_shortlist"] = [c["drink_id"] for c in cocktail_shortlist()]
    return context


def chat_payload(history: list[dict]) -> dict:
    """Recent turns plus client-held session state; unset state and the default domain are
    left out, so fashion requests look exactly as they did before cocktails existed."""
    payload: dict = {"history": [history_item(turn) for turn in history[-10:]]}
    payload |= cocktail_context() if current_domain() == COCKTAILS else fashion_context()
    if policy := auto_approve():
        payload["auto_approve"] = policy
    return payload


def stream_chat(payload: dict, on_step) -> dict | None:
    """POST /chat/stream and report each agent step; return the final reply."""
    try:
        with api_client().stream("POST", "/chat/stream", json=payload) as response:
            if response.is_error:
                response.read()
                try:
                    detail = response.json().get("detail", response.text)
                except ValueError:
                    detail = response.text
                st.error(detail)
                return None
            for line in response.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                kind = event.get("type")
                if kind == "step":
                    on_step(event["step"])
                elif kind == "reply":
                    return event["reply"]
                elif kind == "error":
                    st.error(event.get("detail", "The assistant failed. Please try again."))
                    return None
                else:  # a plain, non-streamed reply
                    return event
    except (httpx.HTTPError, json.JSONDecodeError):
        st.error("The assistant is unavailable. Please try again.")
    return None


def saved_count(count: int, noun: str) -> str:
    return f"Saved {count} {noun if count == 1 else noun + 's'} to your shortlist."


def apply_effects(reply: dict) -> None:
    products = {p["product_id"]: p for p in reply.get("products", [])}
    cocktails = {c["drink_id"]: c for c in reply.get("cocktails", [])}
    for effect in reply.get("applied", []):
        kind = effect["kind"]
        if kind == "shortlist":
            for pid in effect.get("product_ids", []):
                product = products.get(pid) or call_api("GET", f"/products/{pid}")
                if product:
                    add_to_shortlist(product, announce=False)
            count = len(effect.get("product_ids", []))
            st.toast(saved_count(count, "piece"), icon=":material/favorite:")
        elif kind == "cocktail_shortlist":
            for did in effect.get("drink_ids", []):
                cocktail = cocktails.get(did) or call_api("GET", f"/cocktails/{did}")
                if cocktail:
                    add_cocktail(cocktail, announce=False)
            count = len(effect.get("drink_ids", []))
            st.toast(saved_count(count, "drink"), icon=":material/favorite:")
        elif kind == "preferences" and effect.get("preferences"):
            memory = preferences()
            for field in ("sections", "colours"):
                values = [*memory.get(field, []), *effect["preferences"].get(field, [])]
                memory[field] = list(dict.fromkeys(values))
            if effect["preferences"].get("max_price") is not None:
                memory["max_price"] = effect["preferences"]["max_price"]
            st.toast("Curio will remember that for this session.", icon=":material/memory:")
        elif kind == "cocktail_preferences" and effect.get("cocktail_preferences"):
            remember_tastes(effect["cocktail_preferences"])
            st.toast("Curio will remember that for this session.", icon=":material/memory:")


def run_turn(payload: dict, history: list[dict], context: str, user_turn: dict | None) -> None:
    progress = st.empty()
    with progress.container(), st.chat_message("assistant"):
        live = st.status("Curio is planning…", expanded=context != "popup")

        def on_step(step: dict) -> None:
            live.markdown(step_line(step))
            live.update(label=f"{STEP_LABELS.get(step['name'], step['name'])}…")

        reply = stream_chat(payload, on_step)
        if reply is None:
            live.update(label="Stopped", state="error", expanded=False)
            st.caption("Please send your message again to retry.")
            return
    # The finished turn below repeats these steps in its collapsed trace.
    progress.empty()
    apply_effects(reply)
    assistant_turn = {
        "role": "assistant",
        "content": reply["text"],
        "products": reply.get("products", []),
        "cocktails": reply.get("cocktails", []),
        "steps": reply.get("steps", []),
        "status": reply.get("status", "complete"),
        "grounding": reply.get("grounding"),
        "clarification": reply.get("clarification"),
        "pending_action": reply.get("pending_action"),
        "suggestions": reply.get("suggestions", []),
    }
    if user_turn is not None:
        history.append(user_turn)
    history.append(assistant_turn)
    show_chat_turn(assistant_turn, len(history) - 1, context, latest=True)


def chat_available(domain: str, health: dict) -> bool:
    if domain == FASHION:
        return bool(health.get("chat_enabled"))
    if not health.get("cocktails_enabled"):
        return False
    domains = call_api("GET", "/domains") or []
    return any(item.get("id") == COCKTAILS and item.get("chat_enabled") for item in domains)


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
    copy = DOMAIN_COPY[current_domain()]
    health = call_api("GET", "/health")
    if health is None:
        return
    if not chat_available(current_domain(), health):
        st.info(copy.unavailable)
        return
    history = chat_history()
    queued = st.session_state.pop(state_key("queued"), None)
    decision = st.session_state.pop(state_key("decision"), None)
    idle = not history and not queued and decision is None
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
        if decision is not None and decision["index"] < len(history):
            turn = history[decision["index"]]
            action = turn.get("pending_action")
            if action and not turn.get("resolution"):
                payload = chat_payload(history)
                payload["decision"] = {
                    "action": action,
                    "approved": decision["approved"],
                    "edited_args": decision["edits"],
                }
                turn["resolution"] = "Approved." if decision["approved"] else "Declined."
        busy = bool(message) or payload is not None
        for index, turn in enumerate(history):
            show_chat_turn(turn, index, context, latest=index == len(history) - 1 and not busy)
        if payload is not None:
            approved = payload["decision"]["approved"]
            user_turn = {"role": "user", "content": "Approved" if approved else "Declined"}
            show_chat_turn(user_turn, len(history), context)
            run_turn(payload, history, context, user_turn)
        elif message:
            user_turn = {"role": "user", "content": message}
            show_chat_turn(user_turn, len(history), context)
            payload = {"message": message, **chat_payload(history)}
            run_turn(payload, history, context, user_turn)


@st.dialog("Compare pieces", width="large")
def compare_dialog(product_ids: list[int]) -> None:
    products = [p for p in shortlist() if p["product_id"] in product_ids][:4]
    if len(products) < 2:
        st.info("Save at least two pieces to compare them.")
        return
    cheapest = min(p["price"] for p in products)
    for column, product in zip(st.columns(len(products)), products, strict=True):
        with column, st.container(key=f"compare_{product['product_id']}"):
            if (photo := product_photo(product["product_id"])) is not None:
                st.image(photo, width="stretch")
            st.text(product["name"])
            st.markdown(f"**€{product['price']:.2f}**")
            if product["price"] == cheapest:
                st.badge("Lowest price", color="green")
            st.caption(
                " · ".join(
                    plain(str(product[key]))
                    for key in ("product_type", "colour", "section")
                    if product.get(key)
                )
            )
            if product.get("description"):
                st.caption(plain(product["description"]))
    st.caption("Prices are approximate; current stock is unknown.")


def approval_toggles() -> None:
    st.markdown("**Approvals**")
    policy = st.session_state.setdefault("approval_policy", {})
    policy["ask_shortlist"] = st.toggle(
        "Ask before saving to my shortlist",
        value=policy.get("ask_shortlist", True),
        key="session_ask_shortlist",
    )
    policy["ask_memory"] = st.toggle(
        DOMAIN_COPY[current_domain()].ask_memory,
        value=policy.get("ask_memory", True),
        key="session_ask_memory",
    )


def fashion_session_panel() -> None:
    """Client-held agent state: personalization link, approval policy, memory and shortlist."""
    copy = DOMAIN_COPY[FASHION]
    with st.container(key="agent_session", border=True):
        st.subheader("Your session", anchor=False)
        examples = (call_api("GET", "/demo/customers") or [])[:10]
        labels = {e["user_id"]: e["label"] for e in examples}
        options = [None, *labels]
        saved = st.session_state.get("chat_shopper")
        shopper = st.selectbox(
            "Personalize with a shopping history",
            options,
            index=options.index(saved) if saved in options else 0,
            format_func=lambda uid: "Not linked" if uid is None else f"{labels[uid]} (#{uid})",
            key="session_shopper_select",
        )
        st.session_state["chat_shopper"] = shopper
        st.caption("Linked histories unlock “Picks from my shopping history”.")
        approval_toggles()

        st.markdown("**Memory**")
        memory = preferences()
        remembered = [
            *(("sections", value) for value in memory.get("sections", [])),
            *(("colours", value) for value in memory.get("colours", [])),
        ]
        if memory.get("max_price") is not None:
            remembered.append(("max_price", memory["max_price"]))
        if not remembered:
            st.caption(copy.memory_hint)
        with st.container(horizontal=True, key="session_memory"):
            for field, value in remembered:
                st.button(
                    f"Under €{value:g}" if field == "max_price" else str(value),
                    key=f"forget_{field}_{value}",
                    icon=":material/close:",
                    help="Forget this preference",
                    on_click=forget_preference,
                    args=(field, value),
                )

        items = shortlist()
        st.markdown(f"**Shortlist · {len(items)}**")
        if not items:
            st.caption(copy.shortlist_hint)
        for product in items:
            with st.container(
                key=f"shortlist_{product['product_id']}",
                horizontal=True,
                vertical_alignment="center",
            ):
                if (photo := product_photo(product["product_id"])) is not None:
                    st.image(photo, width=40)
                with st.container(gap=None, width="stretch"):
                    st.text(product["name"])
                    st.caption(f"€{product['price']:.2f}")
                st.button(
                    ":material/close:",
                    key=f"shortlist_remove_{product['product_id']}",
                    help=f"Remove {product['name']}",
                    type="tertiary",
                    on_click=remove_from_shortlist,
                    args=(product["product_id"],),
                )
        if len(items) >= 2:
            with st.container(horizontal=True):
                if st.button("Compare", icon=":material/compare:", key="shortlist_compare"):
                    compare_dialog([p["product_id"] for p in items])
                st.button(
                    "Ask Curio",
                    icon=":material/chat_bubble:",
                    key="shortlist_ask",
                    on_click=queue_message,
                    args=(copy.compare_prompt,),
                )


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
    copy = DOMAIN_COPY[COCKTAILS]
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
                    ":material/close:",
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


def session_panel() -> None:
    if current_domain() == COCKTAILS:
        cocktail_session_panel()
    else:
        fashion_session_panel()


def agent_home() -> None:
    copy = DOMAIN_COPY[current_domain()]
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
    copy = DOMAIN_COPY[current_domain()]
    with popup, st.container(key="assistant_panel"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.subheader("Curio", width="stretch", anchor=False)
            st.button("New chat", key="chat_reset_popup", type="tertiary", on_click=reset_chat)
            st.button("Close", key="chat_close", type="tertiary", on_click=close_assistant)
        st.caption(copy.popup_caption)
        chat_conversation("popup")
        st.caption(copy.popup_note)


# --- Cocktails -----------------------------------------------------------------------------

ALCOHOL_FILTERS = {"any": "Any", "alcoholic": "Alcoholic", "non_alcoholic": "Alcohol-free"}
# Catalog `alcoholic` values -> (label, badge colour).
ALCOHOL_STATUS = {
    "Alcoholic": ("Alcoholic", "orange"),
    "Non alcoholic": ("Alcohol-free", "green"),
    "Optional alcohol": ("Optional alcohol", "blue"),
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
        st.image(url, width=width)
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
            query = st.text_input(
                "Describe a drink",
                key="cocktail_query",
                placeholder="e.g. fresh and minty, with lime",
                max_chars=200,
            )
            left, middle, right = st.columns([0.42, 0.33, 0.25], vertical_alignment="bottom")
            with left:
                required = st.multiselect(
                    "Must include",
                    ingredients,
                    key="cocktail_ingredients",
                    max_selections=10,
                    placeholder="Any ingredients",
                )
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
        pantry = st.multiselect(
            "Ingredients you have",
            ingredients,
            key="pantry_ingredients",
            max_selections=15,
            placeholder="e.g. Gin, Lime, Tonic water",
        )
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


# --- App shell -----------------------------------------------------------------------------


def switch_domain() -> None:
    st.session_state["domain"] = st.session_state["domain_switch"]
    st.session_state["domain_switched"] = True


def active_domain(health: dict) -> str:
    """The selected domain, falling back to fashion when cocktails are not served."""
    if current_domain() == COCKTAILS and not health.get("cocktails_enabled"):
        st.session_state["domain"] = FASHION
    return current_domain()


def domain_switch(cocktails_enabled: bool) -> None:
    # The widget mirrors `domain`, which deep links and tests may set directly.
    st.session_state["domain_switch"] = current_domain()
    st.segmented_control(
        "Catalog",
        list(DOMAIN_LABELS),
        format_func=DOMAIN_LABELS.get,
        required=True,
        key="domain_switch",
        on_change=switch_domain,
        disabled=not cocktails_enabled,
        label_visibility="collapsed",
    )
    if not cocktails_enabled:
        st.caption("Cocktails are not available", help=COCKTAILS_UNAVAILABLE)


def domain_pages(domain: str, cocktails_enabled: bool) -> list:
    """Every page stays routable; only the active domain's pages are shown in the menu."""

    def shown(owner: str) -> str:
        return "visible" if owner == domain else "hidden"

    pages = [
        st.Page(agent_home, title="Ask Curio", default=True),
        st.Page(
            recommendations_tab, title="Fashion", url_path="fashion", visibility=shown(FASHION)
        ),
        st.Page(discovery_page, title="Discover", url_path="discover", visibility=shown(FASHION)),
        st.Page(
            knowledge_graph_tab,
            title="Product connections",
            url_path="connections",
            visibility=shown(FASHION),
        ),
    ]
    if cocktails_enabled:
        pages.append(
            st.Page(
                cocktails_page,
                title="Cocktails",
                url_path="cocktails",
                visibility=shown(COCKTAILS),
            )
        )
    return [
        *pages,
        st.Page(evaluation_tab, title="Model quality", url_path="quality", visibility="hidden"),
        st.Page(chat_tab, title="Assistant", url_path="assistant", visibility="hidden"),
    ]


def catalog_note(health: dict) -> str:
    if current_domain() == COCKTAILS:
        count = health.get("cocktail_count")
        recipes = f"{count:,} recipes · " if count else ""
        return f"Recipe data: TheCocktailDB · {recipes}Please drink responsibly."
    indexed = f"{health['indexed_products']:,}"
    total = f" of {health['catalog_products']:,}" if health.get("catalog_products") else ""
    return f"H&M 2020 research catalog · Photo search: {indexed}{total} pieces"


def main() -> None:
    st.set_page_config(page_title="Curio · Agentic recommendations", page_icon="C", layout="wide")
    st.html("<style>" + Path(__file__).with_name("ui.css").read_text() + "</style>")
    health = call_api("GET", "/health")
    header = st.container(
        key="curio_header",
        horizontal=True,
        vertical_alignment="center",
        horizontal_alignment="distribute",
    )
    header.html('<div class="curio-brand">Curio<span>AGENTIC RECOMMENDATIONS</span></div>')
    if health is None:
        st.info("Curio cannot connect right now. Please try again shortly.")
        if st.button("Retry connection"):
            st.rerun()
        return
    st.session_state["service_health"] = health
    cocktails_enabled = bool(health.get("cocktails_enabled"))
    domain = active_domain(health)
    pages = domain_pages(domain, cocktails_enabled)
    page = st.navigation(pages, position="top")
    owner = PAGE_DOMAINS.get(page.url_path)
    switched = st.session_state.pop("domain_switched", False)
    if owner is not None and owner != domain:
        if switched:  # the shopper changed domain on another domain's page: go home
            st.switch_page(pages[0])
        st.session_state["domain"] = owner  # a deep link selects its page's domain
        st.rerun()
    with header:
        domain_switch(cocktails_enabled)
    page.run()
    if page.title not in ("Ask Curio", "Assistant"):
        assistant_popup()
    with st.container(key="catalog_note"):
        st.caption(catalog_note(health))


if __name__ == "__main__":
    main()
