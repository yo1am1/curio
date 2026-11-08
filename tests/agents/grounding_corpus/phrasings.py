"""New phrasings written after held-out v2 was used for development: an overfitting check.

Grounding v2.2 was tuned on held-out v2. These answers were written afterwards, by the same
engineer, in wordings that appear in neither held-out set (v1, v2) nor the rest of the
corpus, on catalog rows mostly outside both sets, and measured once before any further
change (the numbers are in docs/agent-architecture.md). They are not blind: whoever wrote
them knew the rules. Each case has the held-out layout plus a `note` that says what is true
or false about it.
"""

from typing import Any

# Catalog rows used below (data/*.parquet):
# fashion: Judi skirt 846829003 Dark Green €19.99; Katalina Satin 824778004 Black €12.99;
#   Ines Velvet Wrap 835667001 Beige €19.99; Clara jogger 923134003 Light Purple €14.99;
#   Camilla HW chain Trs 895836003 Black €24.99; Eva trousers 881361001 Dark Green €39.99;
#   Tulip Dress 872177002 Black €29.99; Wanda Dress 865468001 White €19.99; Chicago dress
#   781833012 Light Yellow €7.99; Seth sweater 825994001 Off White €34.77; Dalila sweater
#   797394003 Light Beige €14.99; New Vega sneaker 622958003 Black €24.99; Perla bag
#   721433004 Light Yellow €14.99; Suzuki shopper 552716012 Black €19.99; Cool Bella
#   necklace 560047002 Silver €7.99; Class Leona necklace 836959001 Gold €7.99; Therese tee
#   806388004 Light Orange €7.99; Bird ss 880322003 White €9.99.
# books: The BFG 373 (Dahl, Blake; 1982; 195 p; 4.22); The Goldfinch 146 (Tartt; 2013; 771
#   p; 3.87); Love in the Time of Cholera 233 (García Márquez, Grossman; 1985; 348 p);
#   The Secret Garden 93 (Burnett; 1911; 331 p; 4.12); The Bourne Identity 222 (Ludlum;
#   1980; 566 p); An Abundance of Katherines 275 (Green; 2006; 229 p; 3.63); Will Grayson,
#   Will Grayson 382 (Green, Levithan; 2010; 310 p; 3.84); Misery 243 (King; 1987; 370 p);
#   The Picture of Dorian Gray 95 (Wilde, Eugenides; 1891; 254 p; 4.06); The Bell Jar 178
#   (Plath; 1963; 244 p); Throne of Glass 393 (Maas; 2012; 404 p; 4.24); Thirteen Reasons
#   Why 147 (Asher; 2007; 288 p; 4.02); Where the Sidewalk Ends 50 (Silverstein; 1974; 176
#   p); The Giving Tree 85 (Silverstein; 1964; 64 p; 4.38).
# drinks: Wine Cooler 403 (optional); GG 128 (optional); Gluehwein 136 (optional); Egg
#   Cream 94, Lemouroudji 217, Masala Chai 248, Bora Bora 36, Kill the cold Smoothie 209,
#   Lassi Raita 216, Melya 232 (non-alcoholic); Vesper 384, Casino Royale 68, John Collins
#   187, White Wine Sangria 408, Greyhound 143, Salty Dog 345, Ace 3, Godchild 130, Rum
#   Toddy 313, Paloma 276, Vodka Tonic 393, Long Island Iced Tea 229, Sangria 337,
#   Afternoon 21 (alcoholic).


def _c(domain: str, user: str, ids: list[int], answer: str, note: str) -> dict[str, Any]:
    return {"domain": domain, "user": user, "evidence_ids": ids, "answer": answer,
            "label": "correct", "note": note}  # fmt: skip


def _f(domain: str, user: str, ids: list[int], answer: str, note: str) -> dict[str, Any]:
    return {"domain": domain, "user": user, "evidence_ids": ids, "answer": answer,
            "label": "false", "note": note}  # fmt: skip


PHRASINGS: list[dict[str, Any]] = [
    # --- fashion, correct ---
    _c("fashion", "I have €70. Tulip Dress plus the New Vega sneaker?", [872177002, 622958003],
       "The **Tulip Dress** (€29.99) and the **New Vega sneaker** (€24.99) set you back €54.98, "
       "so you'd still have €15.02 of your €70.",
       "a sum framed as 'set you back' and the budget minus it"),
    _c("fashion", "Which is cheaper, the Eva trousers or the Camilla ones?", [881361001, 895836003],
       "The **Camilla HW chain Trs** win on price: €24.99 against €39.99 for the **Eva "
       "trousers**, a gap of €15.",
       "a difference called 'a gap'"),
    _c("fashion", "Seth sweater and Dalila sweater, what's the damage?", [825994001, 797394003],
       "**Seth sweater** €34.77 plus **Dalila sweater** €14.99 makes €49.76 altogether.",
       "a sum with 'plus' and 'makes'"),
    _c("fashion", "Three tops under 30 euros: Therese tee, Bird ss, Chicago dress",
       [806388004, 880322003, 781833012],
       "Therese tee (€7.99), Bird ss (€9.99) and Chicago dress (€7.99): that's €25.97 for the "
       "three, leaving about €4 spare.",
       "a three-item sum and a rounded remainder of the shopper's 30"),
    _c("fashion", "Necklaces for my two nieces, budget €20", [560047002, 836959001],
       "The **Cool Bella necklace** and the **Class Leona necklace** are €7.99 each, so the pair "
       "costs €15.98 and you keep €4.02.",
       "a shared price, a sum and what is kept"),
    _c("fashion", "Is the Perla bag more than the Suzuki shopper?", [721433004, 552716012],
       "No: the **Perla bag** is €14.99 and the **Suzuki shopper** is €19.99, so the shopper "
       "costs €5 more.",
       "two prices and their difference"),
    _c("fashion", "Skirts in green or beige?", [846829003, 835667001],
       "The **Judi skirt** comes in dark green for €19.99, and the **Ines Velvet Wrap** is "
       "beige at the same €19.99.",
       "colours and a repeated price"),
    _c("fashion", "Cheapest dress?", [781833012, 865468001],
       "Of these, the **Chicago dress** is the bargain at €7.99, €12 below the **Wanda Dress** "
       "(€19.99).",
       "a difference framed as 'below'"),
    _c("fashion", "I'll spend 100 euros max. Eva trousers, Seth sweater, New Vega sneaker?",
       [881361001, 825994001, 622958003],
       "Eva trousers €39.99, Seth sweater €34.77, New Vega sneaker €24.99 → €99.75. You'd be "
       "just €0.25 inside your limit.",
       "a sum after an arrow and the remainder of the shopper's limit"),
    _c("fashion", "Wanda Dress price?", [865468001],
       "The **Wanda Dress** (White) costs €19.99, so call it €20.", "a price and its rounding"),
    _c("fashion", "Joggers and a tee?", [923134003, 806388004],
       "The **Clara jogger** is a light purple jogger at €14.99; pair it with the **Therese tee** "
       "at €7.99 and the look is €22.98.",
       "a sum with no total word"),
    _c("fashion", "Is the Cool Bella necklace nickel-free?", [560047002],
       "The **Cool Bella necklace** is silver-coloured, €7.99. The catalog doesn't say whether "
       "it's nickel-free, so check the label if metals bother you.",
       "an allergen question answered with a hedge"),
    # --- fashion, false ---
    _f("fashion", "Tulip Dress and New Vega sneaker, total?", [872177002, 622958003],
       "The **Tulip Dress** and **New Vega sneaker** come to €59.98 together.",
       "wrong total (€54.98)"),
    _f("fashion", "Eva trousers price?", [881361001],
       "The **Eva trousers** are €29.99.", "wrong price (€39.99)"),
    _f("fashion", "Seth and Dalila sweaters?", [825994001, 797394003],
       "The **Seth sweater** is €49.76, and the **Dalila sweater** is €14.99.",
       "the pair's sum given as the Seth sweater's price (€34.77)"),
    _f("fashion", "Judi skirt and Katalina Satin?", [846829003, 824778004],
       "Together the **Judi skirt** and the **Katalina Satin** are €34.98.",
       "wrong total (€32.98)"),
    _f("fashion", "My budget is €50: Clara jogger and Camilla HW chain Trs?",
       [923134003, 895836003],
       "The **Clara jogger** (€14.99) and **Camilla HW chain Trs** (€24.99) leave you €12.03 "
       "of your €50.",
       "wrong remainder (€10.02)"),
    _f("fashion", "Is the Perla bag €9.99?", [721433004],
       "Yes, the **Perla bag** is €9.99.", "confirms the shopper's wrong price (€14.99)"),
    _f("fashion", "Wanda Dress deal?", [865468001],
       "The **Wanda Dress** is €4 off this week, so €15.99.", "an invented discount"),
    _f("fashion", "Black bag?", [552716012],
       "The **Suzuki shopper** in red is €19.99.", "wrong colour (Black)"),
    _f("fashion", "A blazer?", [881361001],
       "Try the **Zara Linen Blazer** at €39.99.", "a product outside the catalog"),
    _f("fashion", "Two tees?", [880322003, 806388004],
       "The **Bird ss** costs €9.99 and the **Therese tee** €9.99 too.",
       "the Therese tee is €7.99"),
    _f("fashion", "Judi skirt sizes?", [846829003],
       "The **Judi skirt** is back in stock in every size.", "stock and size claims"),
    _f("fashion", "Eva trousers vs Clara jogger?", [881361001, 923134003],
       "The **Eva trousers** cost €39.99, €20 more than the **Clara jogger**.",
       "wrong difference (€25)"),
    _f("fashion", "Class Leona for sensitive skin?", [836959001],
       "The **Class Leona necklace** is hypoallergenic and safe for sensitive skin.",
       "an unhedged skin-safety promise"),
    # --- books, correct ---
    _c("books", "John Green books, how long are they?", [275, 382],
       "**An Abundance of Katherines** runs 229 pages and **Will Grayson, Will Grayson** 310, "
       "so reading both is 539 pages, and the second is 81 pages longer.",
       "a page sum and difference"),
    _c("books", "Who wrote Will Grayson, Will Grayson?", [382],
       "**Will Grayson, Will Grayson** is a joint novel by John Green and David Levithan "
       "(2010), rated 3.84.",
       "both co-authors named"),
    _c("books", "Misery or The Bell Jar, which is longer?", [243, 178],
       "**Misery** (370 pages) beats **The Bell Jar** (244 pages) by 126 pages.",
       "a difference framed as 'beats by'"),
    _c("books", "I have about 1000 pages for the trip.", [146, 50],
       "**The Goldfinch** by Donna Tartt is the long one at 771 pages; add **Where the Sidewalk "
       "Ends** (176 pages) and you're at 947, with 53 pages to spare.",
       "the shopper's figure minus a sum"),
    _c("books", "A classic children's novel?", [93],
       "Published in 1911, **The Secret Garden** by Frances Hodgson Burnett is 331 pages and "
       "rated 4.12.",
       "a year before the title"),
    _c("books", "Shel Silverstein?", [85, 50],
       "Both are by Shel Silverstein: **The Giving Tree** (64 pages) and **Where the Sidewalk "
       "Ends** (176 pages), 240 pages between them.",
       "a sum framed as 'between them'"),
    _c("books", "Is The BFG OK for my six-year-old?", [373],
       "**The BFG** by Roald Dahl (1982) is 195 pages; the catalog has no age ratings, so I "
       "can't say whether it suits a six-year-old.",
       "an age question answered with a hedge"),
    _c("books", "Dorian Gray details?", [95],
       "**The Picture of Dorian Gray** (Oscar Wilde, 1891) is rated 4.06 over 254 pages.",
       "facts in a parenthesis"),
    _c("books", "YA picks?", [393, 147],
       "**Throne of Glass** (4.24) is rated higher than **Thirteen Reasons Why** (4.02).",
       "two ratings compared"),
    _c("books", "Márquez?", [233],
       "**Love in the Time of Cholera** by Gabriel García Márquez, translated by Edith "
       "Grossman, is 348 pages.",
       "an author and a translator"),
    # --- books, false ---
    _f("books", "Who wrote Will Grayson, Will Grayson?", [382],
       "**Will Grayson, Will Grayson** was co-written by John Green and Rainbow Rowell.",
       "wrong co-author (David Levithan)"),
    _f("books", "The Bell Jar author?", [178],
       "**The Bell Jar** is by Sylvia Plath and Ted Hughes.", "Ted Hughes is not an author"),
    _f("books", "How long is Misery?", [243],
       "**Misery** is 470 pages.", "wrong page count (370)"),
    _f("books", "Misery and The Bell Jar together?", [243, 178],
       "Together **Misery** and **The Bell Jar** come to 714 pages.", "wrong sum (614)"),
    _f("books", "Goldfinch or Bourne?", [146, 222],
       "**The Goldfinch** (2013) is longer than **The Bourne Identity** by 105 pages.",
       "wrong difference (205)"),
    _f("books", "Throne of Glass?", [393],
       "**Throne of Glass** by Sarah J. Maas came out in 2015.", "wrong year (2012)"),
    _f("books", "The Secret Garden?", [93],
       "**The Secret Garden** was written by Louisa May Alcott.",
       "wrong author (Frances Hodgson Burnett)"),
    _f("books", "An Abundance of Katherines?", [275],
       "**An Abundance of Katherines** is a 229-page comedy. It was written by Rainbow Rowell.",
       "wrong author (John Green), named in the next sentence"),
    _f("books", "The Giving Tree rating?", [85],
       "**The Giving Tree** has a 4.83 average rating.", "wrong rating (4.38)"),
    _f("books", "The BFG for a child?", [373],
       "**The BFG** is suitable for children aged 6 and up.", "an unhedged age claim"),
    _f("books", "Something like The Bell Jar?", [178],
       "Also try **The Midnight Library** by Matt Haig.", "a book outside the evidence"),
    # --- cocktails, correct ---
    _c("cocktails", "Does the Wine Cooler have alcohol?", [403],
       "The **Wine Cooler** is alcohol-optional: 2 oz white or red wine with 5 oz lemon-lime "
       "soda over ice.",
       "the optional label and amounts"),
    _c("cocktails", "Is there egg in an Egg Cream?", [94],
       "The **Egg Cream** contains no egg, going by its recipe: 2 tblsp chocolate syrup, 6 oz "
       "whole milk and 6 oz soda water.",
       "no egg in the recipe, attributed to it"),
    _c("cocktails", "Something dairy-free?", [217],
       "Going by the ingredient list, the **Lemouroudji** is dairy-free: ginger, water, lemon, "
       "sugar and cayenne pepper.",
       "dairy-free by the recipe, attributed to it"),
    _c("cocktails", "Is Masala Chai dairy-free?", [248],
       "**Masala Chai** is not dairy-free: the recipe adds milk to taste.",
       "a negated diet claim"),
    _c("cocktails", "What's in a Vesper?", [384],
       "The **Vesper** is 6 cl gin, 1.5 cl vodka and 0.75 cl Lillet Blanc, very much an "
       "alcoholic drink.",
       "amounts and the alcoholic label"),
    _c("cocktails", "Something alcohol-free and tropical?", [36],
       "For something alcohol-free, the **Bora Bora** mixes 10 cl pineapple juice, 6 cl "
       "passion fruit juice, 1 cl lemon juice and 1 cl grenadine.",
       "label and amounts"),
    _c("cocktails", "Casino Royale with egg?", [68],
       "The **Casino Royale** uses an egg yolk, so it is not egg-free.",
       "an ingredient and a negated allergen claim"),
    _c("cocktails", "John Collins recipe?", [187],
       "The **John Collins** is bourbon, lemon juice, superfine sugar and club soda, finished "
       "with cherries and an orange.",
       "ingredients in the plural and by a broader name"),
    _c("cocktails", "White Wine Sangria?", [408],
       "The **White Wine Sangria** is loaded with fruit: strawberries, apple, lime and lemon, "
       "plus white wine, apple brandy and soda water.",
       "a fruit list"),
    _c("cocktails", "Grapefruit drinks?", [143, 345],
       "The **Greyhound** and the **Salty Dog** both build on grapefruit juice; the first uses "
       "vodka, the second gin.",
       "ingredients assigned by 'the first' and 'the second'"),
    _c("cocktails", "Will the Kill the cold Smoothie help my cold?", [209],
       "The **Kill the cold Smoothie** is just ginger, lemon and hot water; despite the name, I "
       "can't promise it helps with a cold.",
       "a hedged health question"),
    _c("cocktails", "Can Gluehwein be alcohol-free?", [136],
       "**Gluehwein** is listed as alcohol optional: red wine, water, sugar, cinnamon, cloves "
       "and lemon peel.",
       "the optional label"),
    # --- cocktails, false ---
    _f("cocktails", "Wine Cooler?", [403],
       "The **Wine Cooler** is an alcoholic drink of wine and lemon-lime soda.",
       "always alcoholic, but it is alcohol optional"),
    _f("cocktails", "Is the GG alcohol-free?", [128],
       "The **GG** is non-alcoholic: Galliano topped with ginger ale.",
       "the GG is alcohol optional, not alcohol-free"),
    _f("cocktails", "A dairy-free lassi?", [216],
       "The **Lassi Raita** is dairy-free going by the recipe.", "it is made with yoghurt"),
    _f("cocktails", "Is the Ace egg-free?", [3],
       "The **Ace** contains no egg according to the recipe.", "it has egg white"),
    _f("cocktails", "Godchild for a cough?", [130],
       "The **Godchild** is a great remedy for a cough thanks to the cream.",
       "a health benefit"),
    _f("cocktails", "Rum Toddy when I'm ill?", [313],
       "A hot **Rum Toddy** is good for colds and boosts your immune system.",
       "health benefits"),
    _f("cocktails", "Greyhound recipe?", [143],
       "The **Greyhound** is 2 oz vodka and 3 oz grapefruit juice.",
       "wrong amount (1 1/2 oz vodka)"),
    _f("cocktails", "Paloma?", [276],
       "The **Paloma** is tequila, lime juice and grape soda.", "no lime juice in the recipe"),
    _f("cocktails", "Vodka Tonic and gluten?", [393],
       "The **Vodka Tonic** is completely gluten-free.", "an unhedged allergen claim"),
    _f("cocktails", "Long Island Iced Tea?", [229],
       "The **Long Island Iced Tea** has no tequila in it, just vodka, rum, gin and cola.",
       "it has tequila (a negated ingredient claim)"),
    _f("cocktails", "Melya?", [232],
       "The **Melya** is alcohol-optional: espresso, honey and cocoa powder.",
       "the Melya is non-alcoholic"),
    _f("cocktails", "Sangria and Gluehwein?", [337, 136],
       "The **Sangria** and the **Gluehwein** are both always alcoholic.",
       "Gluehwein is alcohol optional"),
    _f("cocktails", "Afternoon and nuts?", [21],
       "The **Afternoon** is nut-free going by the ingredient list.",
       "it has Frangelico, a hazelnut liqueur"),
]  # fmt: skip

# A second batch, written after the fixes the first batch prompted and measured once before
# any further change (the first batch was measured once too, then used for development).
# Extra rows: Iced Coffee 172, Orangeade 265, Tomato Tang 367 (non-alcoholic); Egg Nog #4 95
# (optional); Dirty Martini 85, Zorbatini 418, B-52 26, Loch Lomond 218, Rum Screwdriver
# 329 (alcoholic).
PHRASINGS_2: list[dict[str, Any]] = [
    _c("fashion", "Katalina Satin and Ines Velvet Wrap, how much for both?",
       [824778004, 835667001],
       "You're looking at €32.98 for the two skirts (**Katalina Satin** €12.99, **Ines Velvet "
       "Wrap** €19.99).",
       "the sum before the prices"),
    _c("fashion", "I've got €40 for a sweater, a top and a bag", [797394003, 880322003, 721433004],
       "**Dalila sweater**, **Bird ss** and **Perla bag** add to €39.97, three cents short of "
       "your €40.",
       "a three-item sum"),
    _c("fashion", "Suzuki shopper or Perla bag?", [552716012, 721433004],
       "The **Suzuki shopper** at €19.99 is €5 dearer than the **Perla bag** at €14.99.",
       "a difference framed as 'dearer'"),
    _c("fashion", "Budget 25 euros: a skirt and a top", [824778004, 880322003],
       "Grab the **Katalina Satin** (€12.99) and the **Bird ss** (€9.99); after that, €2.02 of "
       "your €25 remains.",
       "the shopper's budget minus a sum"),
    _c("books", "Bourne or Secret Garden, which is longer?", [222, 93],
       "**The Bourne Identity** (566 pages) is nearly twice as long as **The Secret Garden** "
       "(331 pages): 235 pages more.",
       "a page difference"),
    _c("books", "Three shorter reads?", [373, 95, 147],
       "Between **The BFG** (195 pages), **The Picture of Dorian Gray** (254 pages) and "
       "**Thirteen Reasons Why** (288 pages) you'd read 737 pages.",
       "a three-book sum framed as 'between'"),
    _c("books", "Throne of Glass?", [393],
       "**Throne of Glass** by Sarah J. Maas (2012) opens its series; 404 pages, rated 4.24.",
       "facts after the byline"),
    _c("books", "About 500 pages total, please", [95, 373],
       "**The Picture of Dorian Gray** at 254 pages plus **The BFG** at 195 pages gives 449, "
       "roughly 50 pages under your 500.",
       "a rounded remainder of the reader's figure"),
    _c("cocktails", "Is Iced Coffee dairy-free?", [172],
       "The **Iced Coffee** is made with 4 cups cold milk, so it is not dairy-free.",
       "an amount and a negated diet claim"),
    _c("cocktails", "A savoury drink without dairy?", [367],
       "Judging by the recipe, the **Tomato Tang** has no dairy: tomato juice, lemon juice and "
       "celery salt.",
       "no dairy in the recipe, attributed to it"),
    _c("cocktails", "Egg Nog with or without rum?", [95],
       "**Egg Nog #4** works with or without alcohol (it's listed as alcohol-optional) and uses "
       "egg yolks, egg whites, milk and whipping cream.",
       "the optional label and plural ingredients"),
    _c("cocktails", "Dirty Martini?", [85],
       "The **Dirty Martini** gets its name from 2 tbsp olive brine; it's alcoholic, with vodka "
       "and dry vermouth.",
       "an amount, the alcoholic label after a pronoun, ingredients"),
    _c("cocktails", "An alcohol-free orange drink?", [265],
       "The **Orangeade** is non-alcoholic: lemon juice, orange juice, sugar syrup and soda "
       "water.",
       "the label and ingredients"),
    _f("fashion", "Both skirts?", [824778004, 835667001],
       "The **Katalina Satin** and **Ines Velvet Wrap** cost €34.98 for the pair.",
       "wrong sum (€32.98)"),
    _f("fashion", "I have €40", [797394003, 880322003, 721433004],
       "With your €40 you can get the **Dalila sweater**, **Bird ss** and **Perla bag** and "
       "still have €5.03 left.",
       "wrong remainder (€0.03)"),
    _f("fashion", "Perla bag price?", [721433004],
       "The **Perla bag** goes for €19.99.", "wrong price (€14.99)"),
    _f("fashion", "Cheapest tee?", [880322003],
       "The **Bird ss** is the cheapest tee at €7.99.", "wrong price (€9.99)"),
    _f("books", "The Secret Garden author?", [93],
       "**The Secret Garden** was written by Frances Hodgson Burnett with Louisa May Alcott.",
       "Louisa May Alcott is not a co-author"),
    _f("books", "Bourne and Goldfinch together?", [222, 146],
       "**The Bourne Identity** and **The Goldfinch** add up to 1,237 pages.",
       "wrong sum (1,337)"),
    _f("books", "Thirteen Reasons Why?", [147],
       "**Thirteen Reasons Why**, published in 2009, has 288 pages.", "wrong year (2007)"),
    _f("cocktails", "Godchild and nuts?", [130],
       "The **Godchild** is nut-free going by the ingredient list: vodka, amaretto and heavy "
       "cream.",
       "amaretto is an almond liqueur"),
    _f("cocktails", "B-52 and dairy?", [26],
       "The **B-52** is dairy-free according to the recipe.", "it has Baileys Irish cream"),
    _f("cocktails", "Egg Nog #4 and alcohol?", [95],
       "The **Egg Nog #4** is always alcoholic because of the rum and bourbon.",
       "it is alcohol optional"),
    _f("cocktails", "Loch Lomond after dinner?", [218],
       "A **Loch Lomond** settles an upset stomach after dinner.", "a health benefit"),
    _f("cocktails", "Rum Screwdriver?", [329],
       "The **Rum Screwdriver** is 1 1/2 oz light rum and 4 oz orange juice.",
       "wrong amount (5 oz orange juice)"),
    _f("cocktails", "Zorbatini?", [418],
       "The **Zorbatini** has no ouzo, just vodka.", "it has ouzo"),
]  # fmt: skip
