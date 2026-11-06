"""Curio's Streamlit client: a thin UI over the HTTP API, laid out like the engine.

- `client`: every API request (`call_api`, the chat stream, product and query photos).
- `common`: the domain registry (`DOMAIN_VIEWS`), per-domain copy and state keys, helpers.
- `fashion`, `cocktails`, `books`, `evening`: each domain's pages, chat cards and session
  panel. `effects` applies approved effects to the client-held lists.
- `chat`: the shared chat panel, step trace, grounding badges and approval cards.
- `shell`: the registry entries, navigation, header, domain switch, theming and `main`.

Run it with `streamlit run src/curio/app/ui/main.py`. `curio.app.ui.<name>` resolves a name
from any of these modules, so `from curio.app import ui; ui.books_page()` works; patch a
name in the module that defines or imports it (e.g. `ui.client.api_client`).
"""

from types import ModuleType

from curio.app.ui import books, chat, client, cocktails, common, effects, evening, fashion, shell

_MODULES: tuple[ModuleType, ...] = (
    shell,
    chat,
    evening,
    effects,
    books,
    cocktails,
    fashion,
    common,
    client,
)


def __getattr__(name: str) -> object:
    for module in _MODULES:
        if name in vars(module):
            return vars(module)[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *(name for module in _MODULES for name in vars(module))})
