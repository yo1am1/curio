# Curio documentation

[Product overview and screenshots](../README.md).

## Running and understanding the app

- [Technical guide](technical-guide.md): data preparation, local setup, Docker, architecture, API and tests.
- [Recommendation audit](recommendation-audit.md): repeat-aware ranking, baselines, chronological evaluation and uncertainty.
- [Research direction](recommendation-research.md): retrieval bottlenecks, dataset comparisons and proposed experiments.

The technical guide describes the fashion release. README screenshots show the
current Curio UI; historical development records retain earlier captures. The app
uses historical research data, approximate prices and a partial photo index.
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
