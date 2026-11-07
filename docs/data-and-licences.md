# Data and licences

The code is under the [Apache License 2.0](../LICENSE). If you redistribute Curio or a work
based on it, keep the [NOTICE](../NOTICE) file's credit ("Based on Curio by Yehor Trepalin",
with a link to this repository) in your documentation or credits, as section 4(d) of the
licence requires. To cite the project, use [CITATION.cff](../CITATION.cff). Versions up to
1.0.0 were released under MIT. The data is not part of this repository and is not covered
by that licence:

- **H&M data**: the [H&M Personalized Fashion Recommendations](https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations)
  dataset, licensed by H&M for non-commercial research use. You download it yourself.
  Its customers are real H&M shoppers under pseudonymous ids; `prepare_data` re-keys a
  sample of them (5,000 by default) to small integers. The fashion API serves that sample's
  ids (`/users`) and, per shopper, the purchased products with their dates
  (`/users/{id}`), because the For you, Connections and Evaluation pages are built on them.
  It does not serve the age the dataset records. Purchase dates and baskets of real people
  are still personal data in many jurisdictions: keep an instance with fashion data on
  your own computer (the default), not on a public address.
- **Book data**: [goodbooks-10k](https://github.com/zygmuntz/goodbooks-10k) by Zygmunt
  Zając, published under the [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
  licence. `prepare_books` downloads it from a pinned commit and checks each file's
  SHA-256. Its metadata, shelves and ratings were collected from Goodreads, so Goodreads'
  terms may also apply to them. Covers and book descriptions belong to their publishers and
  Goodreads and are not covered by CC BY-SA: when you run Curio, covers are loaded directly
  from Goodreads by your browser, and descriptions stay in your local prepared file. The
  exception is the README images that show book covers or a description excerpt
  (`docs/screenshots/curio-pairing.webp`, `curio-evening.webp` and the overview animation
  `curio-overview.webp`), for illustration only; that material remains its owners' and is
  not licensed by this repository. The prepared files are not part of this repository; if you share them, credit goodbooks-10k and share them under CC BY-SA
  4.0. The book pages credit "Book data: goodbooks-10k from Goodreads (CC BY-SA 4.0)" and
  link each book to Goodreads.
- **Book covers missing from Goodreads** come from the [Open Library Covers
  API](https://openlibrary.org/dev/docs/api/covers) by ISBN, loaded by your browser; the
  book pages credit "Some covers: Open Library" and the book dialog links each such cover
  to its Open Library page. The covers remain their publishers' material.
- **Cocktail data**: recipes and photos come from [TheCocktailDB](https://www.thecocktaildb.com/).
  `prepare_cocktails` reads them from a CSV in
  [yo1am1/cocktail_advisor](https://github.com/yo1am1/cocktail_advisor), whose code is
  Apache-2.0. The recipe data and images remain subject to TheCocktailDB's own terms;
  Curio does not relicense them, and neither Curio's licence nor cocktail_advisor's covers
  them. Check TheCocktailDB's terms before any commercial use. Drink photos are loaded
  directly from thecocktaildb.com, and the cocktail pages credit "Recipe data: TheCocktailDB".
