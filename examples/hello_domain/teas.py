"""Hello, domain: a tea guide on Curio's agent engine, complete in one file.

A domain brings its items, its tools, a fact sheet and a grounding check; the engine runs the
turn. Here: twelve teas loaded from `teas.csv` (your own dataset goes in the same way, see
docs/extending.md), one READ tool (SearchTeas), one APPROVE tool (SaveTeas: the user
approves, narrows or declines a signed proposal), a fact sheet so answers cite a tea's name
and price as `{tea:4}` and `{price:4}` (the engine renders them from the catalog), and a
backstop that checks the names, IDs and prices the model types itself. `BaseToolkit`
supplies the optional hooks. Run it with any LangChain chat model:
`tea_agent(model).reply("...", [])`.
"""

import csv
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

from curio.engine import ActionSigner, AgentEngine, BaseToolkit, GroundingReport, Policy, Schemas
from curio.engine.facts import Attribute, FactSheet, ItemKind, cite_rules
from curio.engine.grounding import any_of, check_ids, grounding_report
from curio.engine.schemas import AgentStep, AppliedEffect, AssistantReply, TurnContext
from curio.engine.toolkit import ToolError, Turn
from curio.engine.tools import AskShopper

TEAS_CSV = Path(__file__).with_name("teas.csv")


class Tea(BaseModel):
    """One catalog item: a positive integer ID (the engine keys evidence by it) and fields."""

    tea_id: PositiveInt
    name: str = Field(min_length=1)
    kind: str
    notes: str
    price_eur: PositiveFloat  # per 100 g


def load_teas(path: Path = TEAS_CSV) -> list[Tea]:
    """The catalog from a CSV with a header row `tea_id,name,kind,notes,price_eur`. Each row
    is validated by `Tea`; a missing file or a repeated ID stops the load."""
    if not path.exists():
        raise FileNotFoundError(f"Tea catalog not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        teas = [Tea.model_validate(row) for row in csv.DictReader(handle)]
    if len({t.tea_id for t in teas}) != len(teas):
        raise ValueError(f"{path}: tea_id values must be unique")
    return teas


# The engine's models, extended with what this domain keeps in the session and replies.
class TeaContext(TurnContext):
    saved: list[PositiveInt] = Field(default_factory=list, max_length=20)


class TeaEffect(AppliedEffect):
    kind: Literal["saved_teas"] = "saved_teas"
    tea_ids: list[int]
    adds: ClassVar = {"tea_ids": "saved"}  # approved IDs join TeaContext.saved


class TeaReply(AssistantReply):
    teas: list[Tea] = Field(default_factory=list)


class SearchTeas(BaseModel):
    """Find teas by name, kind or flavour words, e.g. 'smoky black'."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=100, pattern=r"\S")


class SaveTeas(BaseModel):
    """Propose saving teas to the user's list. The user approves it first."""

    model_config = ConfigDict(extra="forbid")
    tea_ids: list[PositiveInt] = Field(min_length=1, max_length=5)


# What `{tea:ID}` and `{price:ID}` render to; `{total:price:ID,ID}` sums prices.
TEA_FACTS = {"price": Attribute("price", "tea", lambda t, _: t.price_eur, "€{:.2f}".format, True)}
PROMPT = "You are a friendly tea guide. Recommend only teas the tools returned. " + cite_rules(
    "{tea:ID} for a tea's name and {price:ID} for its price per 100 g",
    "Try {tea:4}, smoky and piney, at {price:4}.",
)
_TEA_ID = re.compile(r"\[id (\d+)\]")
_EUROS = re.compile(r"€\s?(\d+(?:\.\d{1,2})?)")


def _lines(teas: Sequence[Tea]) -> str:
    return "\n".join(f"- [id {t.tea_id}] {t.name}: {t.kind}, {t.notes}" for t in teas)


class TeaToolkit(BaseToolkit[Tea]):
    tools: ClassVar = {schema.__name__: schema for schema in (SearchTeas, AskShopper, SaveTeas)}
    policies: ClassVar = dict(
        SearchTeas=Policy.READ, AskShopper=Policy.ASK, SaveTeas=Policy.APPROVE
    )
    grounding_subject = "tea names, IDs and prices"

    def __init__(self, teas: Sequence[Tea] | None = None) -> None:
        teas = load_teas() if teas is None else teas
        self._teas = {t.tea_id: t for t in teas}
        self._names = any_of([t.name for t in teas], re.IGNORECASE)

    def key(self, item: Tea) -> int:
        return item.tea_id

    def prompt(self, turn: Turn[Tea], message: str, history: Sequence[BaseMessage]):
        saved = self.proposed({"tea_ids": turn.context.saved})
        turn.remember(saved)
        system = f"{PROMPT}\nSaved teas:\n{_lines(saved) or '(none)'}"
        return [SystemMessage(system), *history, HumanMessage(message)]

    def read(self, turn: Turn[Tea], action: SearchTeas) -> tuple[str, AgentStep]:
        words = action.query.lower().split()
        text = {t.tea_id: f"{t.name} {t.kind} {t.notes}".lower() for t in self._teas.values()}
        turn.show([t for t in self._teas.values() if any(w in text[t.tea_id] for w in words)][:5])
        step = AgentStep(kind="tool", name="SearchTeas", summary=f"Found {len(turn.items)} teas.")
        return _lines(turn.items) or "(none)", step

    def apply(self, action: SaveTeas, *, dry_run: bool = False) -> tuple[TeaEffect, list[Tea], str]:
        teas = self.proposed({"tea_ids": list(dict.fromkeys(action.tea_ids))})
        if not teas:
            raise ToolError("None of those tea IDs exist.")
        verb = "Would save" if dry_run else "Saved"
        names = ", ".join(t.name for t in teas)
        return TeaEffect(tea_ids=[t.tea_id for t in teas]), teas, f"{verb} {names}."

    def proposed(self, args: Mapping[str, Any]) -> list[Tea]:
        return [self._teas[i] for i in args.get("tea_ids", []) if i in self._teas]

    def facts(self, turn: Turn[Tea]) -> FactSheet:
        """What an answer may cite: the teas this turn's tools returned (its evidence)."""
        return FactSheet({"tea": ItemKind(lambda t: t.name, dict(turn.evidence))}, TEA_FACTS)

    def check(self, turn: Turn[Tea], text: str) -> GroundingReport:
        """The backstop for what the model typed itself: every tea ID and catalog tea name
        must be one a tool returned, and every euro amount one of their prices."""
        returned = list(turn.evidence.values())
        checked, issues = check_ids(text, _TEA_ID, (t.tea_id for t in returned), "tea")
        for name in dict.fromkeys(m.group(0) for m in self._names.finditer(text)):
            checked += 1
            if name.casefold() not in {t.name.casefold() for t in returned}:
                issues.append(f"{name} was not returned by any tool")
        for amount in _EUROS.findall(text):
            checked += 1
            if float(amount) not in {t.price_eur for t in returned}:
                issues.append(f"€{amount} is not the price of a tea a tool returned")
        return grounding_report(checked, issues)

    def fallback(self, items: Sequence[Tea], extras: Mapping[str, Any] | None = None) -> str:
        """Catalog facts only, stated through references the engine renders."""
        lines = [f"- {{tea:{t.tea_id}}} [id {t.tea_id}]: {{price:{t.tea_id}}}" for t in items]
        return "Teas I could confirm:\n" + "\n".join(lines) if items else "I found no such tea."

    def reply_fields(self, turn: Turn[Tea]) -> dict[str, Any]:
        return {"teas": turn.items}


def tea_agent(
    chat_model: BaseChatModel,
    *,
    signer: ActionSigner | None = None,
    teas: Sequence[Tea] | None = None,
):
    schemas = Schemas(context=TeaContext, reply=TeaReply)
    return AgentEngine(chat_model, TeaToolkit(teas), signer=signer, schemas=schemas)
