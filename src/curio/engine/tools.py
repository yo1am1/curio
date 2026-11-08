"""Tool policies and the one tool every domain shares.

Every tool is a strict pydantic schema: unknown fields, empty queries and out-of-range IDs
are rejected before anything runs. The policy decides what happens to a valid call:

- READ tools only look things up and run immediately.
- ASK is for `AskShopper` only: it stops the turn and hands a question with quick replies
  back to the shopper. (Any other tool marked ASK is handled as an APPROVE tool.)
- APPROVE tools change shopper state (a shortlist, remembered preferences). They never run
  on the model's say-so: the turn stops with a proposal the shopper approves, edits or
  rejects, unless the shopper listed the tool in `TurnContext.auto_approve`.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Policy(StrEnum):
    READ = "read"
    ASK = "ask"
    APPROVE = "approve"


class AskShopper(BaseModel):
    """Ask the shopper one short clarifying question with 2-4 quick-reply options. Use it
    only when the request is too ambiguous to search well (e.g. no audience or occasion for
    a broad request); this ends your turn."""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=200, pattern=r"\S")
    options: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def _clean_options(self) -> "AskShopper":
        options = list(dict.fromkeys(o.strip()[:60] for o in self.options if o.strip()))
        if len(options) < 2:
            raise ValueError("Give at least two distinct options")
        self.options = options
        return self
