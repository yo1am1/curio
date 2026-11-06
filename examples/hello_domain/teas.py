"""Hello, domain: a tea guide on Curio's agent engine, complete in one file.

A domain brings its items, its tools and a grounding check; the engine runs the turn. Here:
twelve teas, one READ tool (SearchTeas), one APPROVE tool (SaveTeas: the shopper approves,
narrows or declines a signed proposal) and a check that every tea name and ID in an answer
came from a tool. `BaseToolkit` supplies the optional hooks. Run it with any LangChain chat
model: `tea_agent(model).reply("...", [])`.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from curio.engine import ActionSigner, AgentEngine, BaseToolkit, GroundingReport, Policy, Schemas
from curio.engine.grounding import any_of, check_ids, grounding_report
from curio.engine.schemas import AgentStep, AppliedEffect, AssistantReply, TurnContext
from curio.engine.toolkit import ToolError, Turn
from curio.engine.tools import AskShopper


class Tea(BaseModel):
    tea_id: int
    name: str
    kind: str
    notes: str


_TEAS = """\
Sencha | green | grassy, fresh, steamed
Dragon Well | green | toasty, chestnut, smooth
Silver Needle | white | delicate, honey, floral
Lapsang Souchong | black | smoky, pine, campfire
Assam | black | malty, strong, breakfast
Darjeeling | black | muscatel, light, floral
Earl Grey | black | bergamot, citrus
Tieguanyin | oolong | orchid, creamy, floral
Da Hong Pao | oolong | roasted, mineral, smoky
Shou Pu-erh | dark | earthy, deep, aged
Rooibos | herbal | caffeine-free, sweet, vanilla
Chamomile | herbal | caffeine-free, apple, calming"""
TEAS = [
    Tea(tea_id=i, name=name, kind=kind, notes=notes)
    for i, (name, kind, notes) in enumerate((r.split(" | ") for r in _TEAS.splitlines()), 1)
]


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
    """Propose saving teas to the shopper's list. The shopper approves it first."""

    model_config = ConfigDict(extra="forbid")
    tea_ids: list[PositiveInt] = Field(min_length=1, max_length=5)


PROMPT = (
    "You are a friendly tea guide. Recommend only teas the tools returned, citing each as "
    "**Name** [id N]. Call SaveTeas when the shopper asks to keep teas."
)
_TEA_ID = re.compile(r"\[id (\d+)\]")


def _lines(teas: Sequence[Tea]) -> str:
    return "\n".join(f"- [id {t.tea_id}] {t.name}: {t.kind}, {t.notes}" for t in teas)


class TeaToolkit(BaseToolkit[Tea]):
    tools: ClassVar = {schema.__name__: schema for schema in (SearchTeas, AskShopper, SaveTeas)}
    policies: ClassVar = dict(
        SearchTeas=Policy.READ, AskShopper=Policy.ASK, SaveTeas=Policy.APPROVE
    )
    grounding_subject = "tea names and IDs"

    def __init__(self, teas: Sequence[Tea] = TEAS) -> None:
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

    def check(self, turn: Turn[Tea], text: str) -> GroundingReport:
        """Every tea ID, and every catalog tea name, must be one a tool returned."""
        returned = list(turn.evidence.values())
        checked, issues = check_ids(text, _TEA_ID, (t.tea_id for t in returned), "tea")
        for name in dict.fromkeys(m.group(0) for m in self._names.finditer(text)):
            checked += 1
            if name.casefold() not in {t.name.casefold() for t in returned}:
                issues.append(f"{name} was not returned by any tool")
        return grounding_report(checked, issues)

    def fallback(self, items: Sequence[Tea], extras: Mapping[str, Any] | None = None) -> str:
        return f"Teas I could confirm:\n{_lines(items)}" if items else "I found no such tea."

    def reply_fields(self, turn: Turn[Tea]) -> dict[str, Any]:
        return {"teas": turn.items}


def tea_agent(chat_model: BaseChatModel, *, signer: ActionSigner | None = None):
    schemas = Schemas(context=TeaContext, reply=TeaReply)
    return AgentEngine(chat_model, TeaToolkit(), signer=signer, schemas=schemas)
