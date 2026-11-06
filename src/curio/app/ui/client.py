"""The HTTP API client: every request the UI makes goes through here."""

import json
from collections.abc import Callable

import httpx
import streamlit as st

from curio.app.config import get_settings


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


def stream_chat(
    payload: dict, on_step, on_error: Callable[[str], object] | None = None
) -> dict | None:
    """POST /chat/stream and report each agent step; return the final reply.

    Failures are reported through on_error (default: shown with st.error) and return None.
    """
    report = on_error or st.error
    try:
        with api_client().stream("POST", "/chat/stream", json=payload) as response:
            if response.is_error:
                response.read()
                try:
                    detail = response.json().get("detail", response.text)
                except ValueError:
                    detail = response.text
                report(detail)
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
                    report(event.get("detail", "The assistant failed. Please try again."))
                    return None
                else:  # a plain, non-streamed reply
                    return event
    except (httpx.HTTPError, json.JSONDecodeError):
        report("The assistant is unavailable. Please try again.")
    return None
