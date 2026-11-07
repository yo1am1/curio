# Curio documentation

[Project overview](../README.md) · [Changelog](../CHANGELOG.md) ·
[Architecture diagram](architecture.svg)

## Guides

- [Technical guide](technical-guide.md): data preparation, local setup, Docker, the code
  layout, the API, tests and releases.
- [Agent architecture](agent-architecture.md): the engine, the toolkits, tool policies,
  signed approvals and grounding checks.
- [Agent evaluation](agent-eval.md): 45 tasks that score a real model's tool choice,
  arguments, grounding, approvals, questions and safety, with a file relay for a model
  without an API key.
- [Multi-domain contract](multidomain-contract.md): data layouts, Python interfaces, agent
  tools, API and UI rules for cocktails, books, outfits, pairings and Plan my evening.
- [Hello, domain](../examples/hello_domain/teas.py): a complete tea domain in one file.
- [Data and licences](data-and-licences.md): where each dataset comes from and its terms.
- [How it was built](how-it-was-built.md): the review loops, test gates and release
  automation.

## Fashion research notes

How the fashion recommender was evaluated and tuned, including the ideas that did not hold.
A gain on one validation week does not mean a model was promoted to the app.

- [Recommendation audit](research/fashion/recommendation-audit.md): baselines,
  chronological evaluation and uncertainty. The README's numbers come from here.
- [Research direction](research/fashion/recommendation-research.md): retrieval bottlenecks
  and proposed experiments.
- [Initial tuning](research/fashion/recommendation-tuning.md)
- [BPR, sampling and training scale](research/fashion/recommendation-iteration2.md)
- [Ranking and customer-disjoint confirmation](research/fashion/recommendation-ranking-followup.md)
- [Complementary retrieval](research/fashion/recommendation-retrieval.md)
- [Candidate provenance](research/fashion/recommendation-provenance.md)

The JSON results and charts behind these notes sit beside them in `research/fashion/`.
