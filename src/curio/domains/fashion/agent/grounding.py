"""Fashion grounding: every euro amount and product ID in an answer must come from evidence."""

import re
from collections.abc import Iterable, Sequence

from curio.domains.fashion.models import Product
from curio.engine.grounding import check_ids, check_prices, grounding_report
from curio.engine.schemas import GroundingReport

_PRODUCT_ID = re.compile(r"\[id (\d+)\]|\bproduct #?(\d{4,})\b|#(\d{6,})\b", re.IGNORECASE)


def check_grounding(
    text: str, evidence: Sequence[Product], extra_amounts: Iterable[float] = ()
) -> GroundingReport:
    """Every price and product ID the answer states must come from tool evidence.

    Allowed amounts are evidence prices, differences between two evidence prices, and
    amounts the shopper or their filters supplied (e.g. a €40 budget).
    """
    priced, price_issues = check_prices(text, (p.price for p in evidence), extra_amounts)
    cited, id_issues = check_ids(text, _PRODUCT_ID, (p.product_id for p in evidence), "product")
    return grounding_report(priced + cited, price_issues + id_issues)
