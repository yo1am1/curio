# Curio documentation

[Project overview and screenshots](../README.md) · [Changelog](../CHANGELOG.md) ·
[Architecture diagram](architecture.svg)

## Guides

- [Technical guide](technical-guide.md): data preparation for every domain, local setup,
  Docker, the code layout, the API, tests and releases.
- [Agent architecture](agent-architecture.md): the domain-free engine, the fashion,
  cocktail, book and evening toolkits, tool policies, signed approvals, grounding checks and
  the review behind them.
- [Multi-domain contract](multidomain-contract.md): the data layouts, Python interfaces,
  agent tools, API and UI rules for cocktails, books, outfits, the book → drink pairing and
  Plan my evening.
- [Hello, domain](../examples/hello_domain/teas.py): a complete tea domain on the engine, in
  one file.

## Research notes

How the fashion recommender was evaluated and tuned. These records document tested ideas and
their limits. An improvement on one validation week does not mean a model was promoted to
the running app.

- [Recommendation audit](recommendation-audit.md): repeat-aware ranking, baselines,
  chronological evaluation and uncertainty. The README's evaluation numbers come from here.
- [Research direction](recommendation-research.md): retrieval bottlenecks, dataset
  comparisons and proposed experiments.
- [Initial tuning](recommendation-tuning.md)
- [BPR, sampling and training scale](recommendation-iteration2.md)
- [Ranking and customer-disjoint confirmation](recommendation-ranking-followup.md)
- [Complementary retrieval](recommendation-retrieval.md)
- [Candidate provenance](recommendation-provenance.md)
- [`research/`](research): the JSON results and charts behind these notes.

Fashion uses historical research data, approximate prices and a photo index covering 14,280
of 14,300 products. Cocktails use 425 recipes from TheCocktailDB, whose data terms are
TheCocktailDB's own. Books use goodbooks-10k (CC BY-SA 4.0, collected from Goodreads). No
hosted demo is available.

## History

[Engineering workflow](engineering-workflow.md) and [session handoff](session-handoff.md)
are chronological records of how Curio was built. Their test counts, branch names, paths
and screenshots describe the work at the time; they are not current setup instructions.
