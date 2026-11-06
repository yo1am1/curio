"""A small book catalog and recipe set that exercise every pairing rule."""

import pytest

from curio.domains.books.catalog import BookCatalog
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.pairings.pairer import BookCocktailPairer
from tests.conftest import book_frame, cocktail_frame

A, N, OPT = "Alcoholic", "Non alcoholic", "Optional alcohol"
# (drink_id, name, alcoholic, category, glass, ingredients)
PAIRING_DRINKS = [
    (1, "Old Fashioned", A, "Cocktail", "Old-fashioned glass",
     [("Bourbon", "2 oz"), ("Angostura Bitters", "2 dashes"), ("Sugar", "1 tsp")]),
    (2, "Manhattan", A, "Cocktail", "Cocktail glass",
     [("Bourbon", "2 oz"), ("Sweet Vermouth", "1 oz"), ("Angostura Bitters", "1 dash")]),
    (3, "Whiskey Sour", A, "Ordinary Drink", "Old-fashioned glass",
     [("Blended whiskey", "2 oz"), ("Lemon", "1/2"), ("Powdered sugar", "1 tsp")]),
    (4, "Martini", A, "Cocktail", "Cocktail glass",
     [("Gin", "2 oz"), ("Dry Vermouth", "1/3 oz"), ("Olive", "1")]),
    (5, "French 75", A, "Ordinary Drink", "Collins Glass",
     [("Gin", "1 1/2 oz"), ("Champagne", "4 oz"), ("Lemon juice", "1/2 oz")]),
    (6, "Kir Royale", A, "Ordinary Drink", "Champagne flute",
     [("Creme de Cassis", "1 tsp"), ("Champagne", "5 oz")]),
    (7, "Espresso Martini", A, "Cocktail", "Cocktail glass",
     [("Vodka", "2 oz"), ("Kahlua", "1/2 oz"), ("Espresso", "1 oz")]),
    (8, "Mai Tai", A, "Ordinary Drink", "Collins Glass",
     [("Light rum", "1 oz"), ("Orgeat syrup", "1/2 oz"), ("Pineapple juice", "2 oz")]),
    (9, "Honey Bee", A, "Cocktail", "Margarita glass",
     [("White Rum", "2 oz"), ("Honey", "1 tbsp"), ("Lemon juice", "1/2 oz")]),
    (10, "Grand Blue", A, "Ordinary Drink", "Old-fashioned glass",
     [("Malibu rum", "1 oz"), ("Blue Curacao", "1 oz"), ("Peach schnapps", "1 oz")]),
    (11, "Hot Toddy", A, "Cocktail", "Irish coffee cup",
     [("Whiskey", "2 oz"), ("Honey", "1 tbsp"), ("Cinnamon", "1 stick"), ("Cloves", "2")]),
    (12, "Corpse Reviver", A, "Cocktail", "Cocktail glass",
     [("Gin", "1 oz"), ("Lillet Blanc", "1 oz"), ("Absinthe", "1 dash")]),
    (13, "Hot Chocolate to Die for", N, "Cocoa", "Coffee mug",
     [("Chocolate", "2 oz"), ("Milk", "1 cup"), ("Marshmallows", "4")]),
    (14, "Iced Coffee", N, "Coffee / Tea", "Coffee mug",
     [("Coffee", "1 cup"), ("Milk", "1/2 cup"), ("Sugar", "1 tsp")]),
    (15, "Fruit Cooler", N, "Other / Unknown", "Highball glass",
     [("Strawberries", "4"), ("Apple juice", "1 cup"), ("Soda Water", "1 cup")]),
    (16, "Gluehwein", OPT, "Punch / Party Drink", "Irish coffee cup",
     [("Red wine", "1 bottle"), ("Cinnamon", "1 stick"), ("Cloves", "4")]),
    (17, "B-52", A, "Shot", "Shot glass",
     [("Kahlua", "1/3 oz"), ("Baileys irish cream", "1/3 oz"), ("Grand Marnier", "1/3 oz")]),
    (18, "Zombie", A, "Cocktail", "Hurricane glass",
     [("151 proof rum", "1 oz"), ("Dark rum", "1 oz"), ("Absinthe", "1 dash")]),
    (19, "Masala Chai", N, "Coffee / Tea", "Coffee mug",
     [("Tea", "2 tsp"), ("Ginger", "1 slice"), ("Cardamom", "2"), ("Milk", "1 cup")]),
    (20, "Vesper", A, "Cocktail", "Cocktail glass",
     [("Gin", "3 oz"), ("Vodka", "1 oz"), ("Lillet Blanc", "1/2 oz")]),
    (21, "Sazerac", A, "Ordinary Drink", "Old-fashioned glass",
     [("Bourbon", "2 oz"), ("Peychaud bitters", "3 dashes"), ("Ricard", "1 dash")]),
    (22, "Mint Julep", A, "Ordinary Drink", "Highball glass",
     [("Bourbon", "2 oz"), ("Mint", "4 leaves"), ("Sugar", "1 tsp")]),
    (23, "Gin Rickey", A, "Ordinary Drink", "Highball glass",
     [("Gin", "2 oz"), ("Lime", "1/2"), ("Soda Water", "4 oz")]),
]  # fmt: skip

# Laid out like tests.conftest.BOOK_ROWS.
PAIRING_BOOKS = [
    (1, "The Big Sleep", ["Raymond Chandler"], None, None, 1939, 4.0, 90_000, 231,
     ["Mystery", "Classics"], ["noir", "crime fiction", "detective"], True),
    (2, "Gone Girl", ["Gillian Flynn"], None, None, 2012, 4.03, 1_600_000, 399,
     ["Thriller", "Mystery"], ["suspense", "psychological thriller"], True),
    (3, "The Great Gatsby", ["F. Scott Fitzgerald"], None, None, 1925, 3.89, 2_600_000, 200,
     ["Classics"], ["classics", "romance"], True),
    (4, "The Name of the Wind", ["Patrick Rothfuss"], "The Kingkiller Chronicle", "1", 2007,
     4.55, 400_000, 662, ["Fantasy"], ["epic fantasy", "magic"], True),
    (5, "Dune", ["Frank Herbert"], None, None, 1965, 4.19, 500_000, 604,
     ["Science fiction"], ["space opera", "sci fi"], True),
    (6, "Harry Potter and the Sorcerer's Stone", ["J.K. Rowling"], None, None, 1997, 4.44,
     4_600_000, 320, ["Fantasy", "Young adult"], ["magic", "ya"], True),
    (7, "The Hobbit", ["J.R.R. Tolkien"], None, None, 1937, 4.25, 2_000_000, 366,
     ["Fantasy", "Classics"], ["fantasy", "adventure", "childrens"], True),
    (8, "Dracula", ["Bram Stoker"], None, None, 1897, 3.98, 700_000, 488,
     ["Horror", "Classics"], ["gothic", "vampires", "horror"], True),
    (9, "Pride and Prejudice", ["Jane Austen"], None, None, 1813, 4.24, 2_000_000, 279,
     ["Romance", "Classics"], ["romance", "regency"], True),
    (10, "Casino Royale", ["Ian Fleming"], None, None, 1953, 3.73, 50_000, 181,
     ["Thriller"], ["spy", "espionage"], True),
    (11, "A Christmas Carol", ["Charles Dickens"], None, None, 1843, 4.03, 600_000, 104,
     ["Classics"], ["christmas", "holiday"], True),
    (12, "An Unshelved Book", ["Nobody Known"], None, None, 2001, 3.5, 20_000, 300,
     [], [], True),
]  # fmt: skip
DESCRIPTIONS = {
    3: "This exemplary novel of the Jazz Age has been acclaimed by generations of readers.",
    5: "Set in the far future amidst a sprawling interstellar empire on a desert planet.",
    11: "Ebenezer Scrooge is visited by ghosts on Christmas Eve.",
    12: "A quiet book about nothing in particular.",
}


def pairing_books() -> BookCatalog:
    frame = book_frame(PAIRING_BOOKS)
    for book_id, text in DESCRIPTIONS.items():
        frame.loc[frame["book_id"] == book_id, "description"] = text
    return BookCatalog(frame)


def pairing_cocktails() -> CocktailCatalog:
    return CocktailCatalog(cocktail_frame(PAIRING_DRINKS))


@pytest.fixture
def pairer() -> BookCocktailPairer:
    cocktails = pairing_cocktails()
    return BookCocktailPairer(pairing_books(), cocktails, CocktailRecommender(cocktails))
