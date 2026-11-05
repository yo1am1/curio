# Curio documentation

[Product overview and screenshots](../README.md) · [Changelog](../CHANGELOG.md).

## Running and understanding the app

- [Technical guide](technical-guide.md): data preparation for every domain, local setup, Docker, architecture, API and tests.
- [Agent architecture](agent-architecture.md): the shared agent engine, the fashion, cocktail and book toolkits, tool policies, human-in-the-loop approvals, grounding checks and the review behind them.
- [Multi-domain contract](multidomain-contract.md): the cocktail and book data layouts, Python interfaces, agent tools, API and UI rules shared by the domains.
- [Recommendation audit](recommendation-audit.md): repeat-aware ranking, baselines, chronological evaluation and uncertainty.
- [Research direction](recommendation-research.md): retrieval bottlenecks, dataset comparisons and proposed experiments.

The technical guide describes version 1.0: fashion and cocktails. README screenshots
show the current Curio UI; historical development records retain earlier captures.
Fashion uses historical research data, approximate prices and a photo index covering 14,280 of 14,300 products.
Cocktails use 425 recipes from TheCocktailDB, whose data terms are TheCocktailDB's own.
Books use goodbooks-10k (CC BY-SA 4.0, collected from Goodreads).
No hosted demo is available yet.

## Experiment records

These records document tested ideas and their limitations. An improvement on one
validation week does not mean a model was promoted to the running app.

- [Initial tuning](recommendation-tuning.md)
- [BPR, sampling and training scale](recommendation-iteration2.md)
- [Ranking and customer-disjoint confirmation](recommendation-ranking-followup.md)
- [Complementary retrieval](recommendation-retrieval.md)
- [Candidate provenance](recommendation-provenance.md)

## Development history

[Engineering workflow](engineering-workflow.md) and [session handoff](session-handoff.md)
are chronological records. Their old test counts, branch names and screenshots
describe the work at the time; they are not current setup instructions.
