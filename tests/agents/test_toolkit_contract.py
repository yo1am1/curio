"""The fashion, cocktail, book and example toolkits pass the static Toolkit contract, and
the checker catches the mistakes it is there for."""

from types import MappingProxyType

import pytest
from pydantic import BaseModel, ConfigDict

from curio.domains.books.agent.schemas import BookContext
from curio.domains.books.agent.toolkit import BookToolkit
from curio.domains.cocktails.agent.schemas import CocktailContext
from curio.domains.cocktails.agent.toolkit import CocktailToolkit
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.fashion.agent.intent import SearchIntent
from curio.domains.fashion.agent.schemas import FashionContext
from curio.domains.fashion.agent.toolkit import FashionToolkit
from curio.engine import Policy
from examples.hello_domain.teas import TeaContext, TeaToolkit
from tests.agents.toolkit_contract import PROTOCOL_METHODS, check_toolkit


def test_the_checker_reads_the_protocol():
    assert {"read", "apply", "check", "facts", "fallback", "reply_fields"} <= set(PROTOCOL_METHODS)


def test_the_example_domain_passes():
    check_toolkit(TeaToolkit(), TeaContext())


def test_the_cocktail_toolkit_passes(cocktail_catalog):
    check_toolkit(
        CocktailToolkit(cocktail_catalog, CocktailRecommender(cocktail_catalog)), CocktailContext()
    )


def test_the_book_toolkit_passes(book_catalog, book_recommender):
    check_toolkit(BookToolkit(book_catalog, book_recommender), BookContext())


def test_the_fashion_toolkit_passes(search, catalog):
    check_toolkit(
        FashionToolkit(search, catalog, 5, object(), None),
        FashionContext(),
        SearchIntent(query="hello"),
    )


class _Loose(BaseModel):
    """Looks teas up."""

    query: str


class _Strict(BaseModel):
    """Looks teas up."""

    model_config = ConfigDict(extra="forbid")
    query: str


class _Undocumented(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


@pytest.mark.parametrize(
    ("tools", "policies", "problem"),
    [
        ({"_Loose": _Loose}, {"_Loose": Policy.READ}, "reject unknown fields"),
        ({"_Undocumented": _Undocumented}, {"_Undocumented": Policy.READ}, "needs a docstring"),
        ({"Lookup": _Strict}, {"Lookup": Policy.READ}, "schema's name"),
        ({"_Strict": _Strict}, {}, "exactly one policy"),
        ({"_Strict": _Strict}, {"_Strict": Policy.ASK}, "ASK is for AskShopper only"),
        ({"_Strict": _Strict}, {"_Strict": Policy.APPROVE}, "no READ tool"),
    ],
)
def test_the_checker_names_what_is_wrong(tools, policies, problem):
    class Broken(TeaToolkit):
        pass

    Broken.tools = MappingProxyType(tools)
    Broken.policies = MappingProxyType(policies)
    with pytest.raises(AssertionError, match=problem):
        check_toolkit(Broken())


def test_a_repair_instruction_must_take_the_issues():
    class Silent(TeaToolkit):
        repair_instruction = "Try again."

    with pytest.raises(AssertionError, match="issues"):
        check_toolkit(Silent())
