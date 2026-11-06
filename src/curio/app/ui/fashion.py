"""Fashion pages: the personalized feed, discovery by text or photo, product connections,
model quality, and the fashion chat cards, outfit card and session panel."""

import math
import re

import streamlit as st

from curio.app.ui.client import call_api, product_photo, query_photo
from curio.app.ui.common import (
    FASHION,
    FASHION_COPY,
    GRID_COLUMNS,
    MAX_SHORTLIST,
    approval_toggles,
    plain,
    queue_message,
    remember_input,
    restore_input,
)

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


def product_name(product: dict) -> str:
    """A product's name as shown: the API's cleaned `display_name` ("PQ BOSTON SILK SKIRT"
    -> "Boston Silk Skirt"), or the raw name from an older API."""
    return product.get("display_name") or product["name"]


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
                    st.image(photo, caption=product_name(product), width="stretch")
                else:
                    with st.container(key=f"missing_photo_{context}_{product['product_id']}_{i}"):
                        st.caption("No photo available.", text_alignment="center")
                with st.container(
                    key=f"product_info_{context}_{product['product_id']}_{i}", gap=None
                ):
                    st.text(product_name(product))
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
                    f"View {product_name(product)}",
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


NO_CUSTOMER_SELECTED = (
    "No shopping history is selected. Open Shopping history to see why or to choose one."
)


def customer_selector(label: str, key: str) -> dict | None:
    """Start with a few explained examples; fetch the full ID list only on request."""
    examples = call_api("GET", "/demo/customers") or []
    examples_by_id = {example["user_id"]: example for example in examples[:10]}
    saved_mode = f"saved_{key}_browse_all"
    selected = st.session_state.get("selected_customer")
    # Without example customers, browsing all is the only way in.
    default_all = st.session_state.get(saved_mode, not examples_by_id) or (
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
        user_id = example["user_id"] if example else None
        profile = call_api("GET", f"/users/{user_id}") if example else None
        if profile:
            purchases = profile["purchases"]
            st.caption(f"Shopping history #{user_id}")
            if purchases:
                with st.expander("Recent purchases (up to 10)"):
                    st.dataframe(
                        [
                            {
                                "Product": product_name(item["product"]),
                                "Type": item["product"]["product_type"],
                                "Price (€)": item["product"]["price"],
                                "Date": item.get("purchase_date", ""),
                            }
                            for item in purchases[:10]
                        ],
                        hide_index=True,
                        width="stretch",
                    )
    if example is None:
        st.info(NO_CUSTOMER_SELECTED)
        return
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
                st.image(photo, caption=product_name(product), width="stretch")
            else:
                st.info("A photo is not available for this item.")
        with right:
            st.subheader(product_name(product))
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
                        f"What goes with the {product_name(product)} (product {product_id})?",
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
            st.text(f"{product_name(item)} · {item['colour']} · €{item['price']:.2f}")
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
        st.info(NO_CUSTOMER_SELECTED)
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
    selected_label = product_name(product)
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
                restore_input("text_query")
                query = st.text_input(
                    "Describe what you are looking for",
                    key="text_query",
                    placeholder="e.g. a chunky cream cardigan",
                )
                remember_input("text_query", query)
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


def shortlist() -> list[dict]:
    return st.session_state.setdefault("shortlist", [])


def add_to_shortlist(product: dict, announce: bool = True) -> bool:
    """Add a piece unless it is saved already or the list is full; return whether it was added."""
    items = shortlist()
    if any(item["product_id"] == product["product_id"] for item in items):
        return False
    if len(items) >= MAX_SHORTLIST:
        if announce:
            st.toast(f"Your shortlist holds up to {MAX_SHORTLIST} pieces.", icon=":material/info:")
        return False
    items.append(product)
    if announce:
        st.toast(f"Saved {product_name(product)} to your shortlist.", icon=":material/favorite:")
    return True


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
                st.image(photo, caption=product_name(product), width=56 if compact else "stretch")
            with st.container(gap=None):
                st.text(product_name(product))
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


# The current outfit's client-held state (fashion only), sent back so the agent can swap or
# save its pieces.
OUTFIT_STATE = "chat_outfit"
SAVE_OUTFIT = "Save the whole outfit to my shortlist"


def swap_prompt(label: str) -> str:
    return f"Swap the {label.lower()} in my outfit"


def cheaper_prompt(label: str) -> str:
    return f"Cheaper {label.lower()} for my outfit"


def budget_line(total: float, budget: float | None) -> str:
    if budget is None:
        return f"Total €{total:.2f}"
    left = budget - total
    rest = f"€{left:.2f} left" if left >= 0 else f"€{-left:.2f} over"
    return f"€{total:.2f} of €{budget:.2f} · {rest}"


def outfit_card(outfit: dict, index: int, context: str, latest: bool) -> None:
    """Slots in a row with photos and reasons, the total against the budget, and (on the
    latest turn) Swap, Cheaper and Save outfit, which send follow-ups to the agent."""
    compact = context == "popup"
    slots = outfit["slots"]
    with st.container(key=f"outfit_{context}_{index}", border=True):
        st.markdown(f":material/checkroom: **Outfit · {plain(outfit['occasion'])}**")
        columns = [st.container() for _ in slots] if compact else st.columns(len(slots))
        for column, slot in zip(columns, slots, strict=True):
            label = slot["label"]
            with (
                column,
                st.container(
                    key=f"outfit_slot_{context}_{index}_{slot['slot']}",
                    horizontal=compact,
                    vertical_alignment="center" if compact else "top",
                ),
            ):
                product = slot.get("product")
                if product is None:
                    with st.container(gap=None):
                        st.markdown(f"**{label}**")
                        st.caption(f":material/block: {plain(slot.get('issue', ''))}")
                    continue
                pid = product["product_id"]
                if (photo := product_photo(pid)) is not None:
                    # Capped, so stacked slots stay short on a phone; columns are narrower.
                    st.image(photo, width=56 if compact else 180)
                with st.container(gap=None):
                    st.markdown(f"**{label}**")
                    st.text(product_name(product))
                    st.caption(f"€{product['price']:.2f} · {product['colour']}")
                if latest:
                    # Short text-only buttons fit side by side in a narrow slot column.
                    with st.container(horizontal=True, gap="small"):
                        st.button(
                            "Swap",
                            key=f"outfit_swap_{context}_{index}_{slot['slot']}",
                            type="tertiary",
                            help=f"Ask Curio for another {label.lower()}",
                            on_click=queue_message,
                            args=(swap_prompt(label),),
                        )
                        st.button(
                            "Cheaper",
                            key=f"outfit_cheaper_{context}_{index}_{slot['slot']}",
                            type="tertiary",
                            help=f"Ask Curio for a cheaper {label.lower()}",
                            on_click=queue_message,
                            args=(cheaper_prompt(label),),
                        )
                if slot.get("reason") and not compact:
                    st.caption(plain(slot["reason"]))
        total, budget = outfit["total"], outfit.get("budget")
        with st.container(key=f"outfit_total_{context}_{index}"):
            if budget:
                st.progress(min(total / budget, 1.0), text=budget_line(total, budget))
            else:
                st.caption(budget_line(total, None))
        for note in outfit.get("notes", []):
            st.caption(plain(note))
        if latest and any(slot.get("product") for slot in slots):
            st.button(
                "Save outfit",
                key=f"outfit_save_{context}_{index}",
                icon=":material/favorite:",
                type="primary",
                help="Curio proposes saving every piece; you approve or untick pieces.",
                on_click=queue_message,
                args=(SAVE_OUTFIT,),
            )


def fashion_context() -> dict:
    context: dict = {}
    memory = {key: value for key, value in preferences().items() if value}
    if memory:
        context["preferences"] = memory
    if st.session_state.get("chat_shopper"):
        context["shopper_id"] = st.session_state["chat_shopper"]
    if shortlist():
        context["shortlist"] = [p["product_id"] for p in shortlist()]
    if outfit := st.session_state.get(OUTFIT_STATE):
        context["outfit"] = outfit
    return context


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
                st.image(photo, caption=product_name(product), width="stretch")
            st.text(product_name(product))
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


def fashion_session_panel() -> None:
    """Client-held agent state: personalization link, approval policy, memory and shortlist."""
    copy = FASHION_COPY
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
                    st.image(photo, caption=product_name(product), width=40)
                with st.container(gap=None, width="stretch"):
                    st.text(product_name(product))
                    st.caption(f"€{product['price']:.2f}")
                st.button(
                    "Remove",
                    icon=":material/close:",
                    key=f"shortlist_remove_{product['product_id']}",
                    help=f"Remove {product_name(product)}",
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


def fashion_note(health: dict) -> str:
    indexed = f"{health.get('indexed_products', 0):,}"
    total = f" of {health['catalog_products']:,}" if health.get("catalog_products") else ""
    return f"H&M 2020 research catalog · Photo search: {indexed}{total} pieces"
