# Curio: agentic recommendations for fashion and cocktails

[![CI](https://github.com/yo1am1/curio/actions/workflows/ci.yml/badge.svg?branch=release%2Fv1.0)](https://github.com/yo1am1/curio/actions/workflows/ci.yml)

Curio recommends things through browsing pages and a tool-using assistant. Version 1.0
has two domains, switched in the header:

- **Fashion**: personalized recommendations, photo search and product connections built
  from historical H&M purchases and product photos.
- **Cocktails**: search, similar drinks and "What can I make?" over 425 recipes from
  TheCocktailDB, served offline from a prepared file.

Both domains share one agent: it plans, calls policy-gated catalog tools, asks before
changing your shortlist or memory, and checks its answer against what the tools returned.

![Curio fashion recommendations](docs/screenshots/curio-fashion.jpg)

## Ask Curio

Describe what you want, set a budget, then refine your search or compare items. In
fashion the agent searches the catalog, finds pieces bought together or alike, and ranks
picks from a linked shopping history. In cocktails it searches recipes, finds similar
drinks and matches what you have at home. Each answer shows the steps it took. Prices,
product and drink IDs, drink names and recipe amounts are checked against the tool
results before you see them. It asks a quick question when a request is vague, and asks
before saving to your shortlist or remembering preferences. It is available from the
conversation home and a floating panel on browsing pages.
[How the agent works](docs/agent-architecture.md).

![Curio floating shopping assistant](docs/screenshots/curio-assistant.jpg)

## Fashion

### Search by description or photo

Search in everyday language, upload a photo, or try an example garment.

![Description search results](docs/screenshots/curio-discover-text.jpg)

![Photo search results](docs/screenshots/curio-discover-photo.jpg)

### Explore product connections

Browse co-purchased pieces, visually similar products and higher-priced alternatives.
Open a product to inspect its details and the evidence behind its recommendations.

![Product connections](docs/screenshots/curio-connections.jpg)

![Product details and related pieces](docs/screenshots/curio-product-detail.jpg)

### On your phone

![Fashion feed on mobile](docs/screenshots/curio-mobile.jpg)

[Conversation home](docs/screenshots/curio-home.jpg) ·
[Mobile conversation home](docs/screenshots/curio-home-mobile.jpg)

## Cocktails

Search recipes by name, flavour words ("fresh", "creamy") or ingredients, filter by
alcohol and category, and open a recipe to see its method and similar drinks. Each
result says why it matched. Alcohol-free means TheCocktailDB's "Non alcoholic" label;
"Alcohol optional" recipes count as alcoholic.

![Cocktail search for "Something fresh with lime and mint"](docs/screenshots/curio-cocktails.jpg)

"What can I make?" ranks drinks by the ingredients you have, shows what is missing and
lets you allow up to three missing ingredients.

![What can I make? with gin, lime and tonic water](docs/screenshots/curio-cocktails-pantry.jpg)

Ingredient filters and avoided ingredients only cover the recipe's listed ingredients.
They are not an allergen or dietary guarantee. Please drink responsibly.

## Under the hood

Streamlit UI → FastAPI → a shared agent engine with one toolkit per domain.
Fashion uses CLIP search, Qdrant, purchase-based recommenders and a learned ranker.
Cocktails use an in-memory TF-IDF and ingredient-overlap recommender; no model or
network call is needed to serve them.

The fashion screenshots use 5,000 historical customers and a partial photo index
covering 3,537 of 14,300 catalog products. Prices are approximate historical values;
there is no live stock or checkout.

On the 819-customer holdout, MAP@12 was 0.0302 versus 0.0248 for buy-again;
customer hit rate was 13.9% versus 11.1%. The uncertainty interval crosses zero,
so this result alone does not establish superiority. [Evaluation and limitations](docs/recommendation-audit.md).
Model quality remains available at `/quality`, outside shopping navigation.

## Run locally

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
cp .env.example .env
uv run python -m scripts.prepare_cocktails     # cocktail recipes: ~400 KB download
```

`prepare_cocktails` downloads a pinned recipe file, checks its SHA-256 and writes
`data/cocktails.parquet`. Without that file the API serves fashion only and the
Cocktails switch is disabled.
For fashion, [prepare the H&M data and image index](docs/technical-guide.md#quick-start).
Then run these commands in separate terminals:

```bash
uv run uvicorn clothes_recommender.api:create_app --factory
uv run streamlit run src/clothes_recommender/ui.py
```

Open http://localhost:8501. Configure an LLM provider in `.env` to enable chat; search,
recommendations and the cocktail pages work without one.
[Docker](docs/technical-guide.md#docker) is also supported.

[Data, architecture, Docker and API reference](docs/technical-guide.md) ·
[LLM setup](docs/technical-guide.md#llm-configuration) ·
[Tests and development](docs/technical-guide.md#development) ·
[Changelog](CHANGELOG.md) ·
[Documentation and research](docs/README.md)

## Data and licences

The code is under the [MIT licence](LICENSE). The data is not part of this repository
and is not covered by that licence:

- **H&M data**: the [H&M Personalized Fashion Recommendations](https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations)
  dataset, licensed by H&M for non-commercial research use. You download it yourself.
- **Cocktail data**: recipes and photos come from [TheCocktailDB](https://www.thecocktaildb.com/).
  `prepare_cocktails` reads them from a CSV in
  [yo1am1/cocktail_advisor](https://github.com/yo1am1/cocktail_advisor), whose code is
  Apache-2.0. The recipe data and images remain subject to TheCocktailDB's own terms;
  Curio does not relicense them, and neither the MIT nor the Apache-2.0 licence covers
  them. Check TheCocktailDB's terms before any commercial use. Drink photos are loaded
  directly from thecocktaildb.com, and the cocktail pages credit "Recipe data: TheCocktailDB".

## Contributors

Created and maintained by [Yehor Trepalin](https://github.com/yo1am1).
AI development assistance: Claude Code, Codex and Antigravity.
