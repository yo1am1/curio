"""Display names for H&M products, checked on 30 real catalog names."""

import pytest

from curio.domains.fashion.models import Product
from curio.domains.fashion.names import display_name

REAL_NAMES = [
    ("Lyra HW wide full(1)", "Lyra HW wide full"),
    ("Eli slim cropped trs", "Eli slim cropped"),
    ("Class GP Britta ring pk", "Class GP Britta ring"),
    ("Iris petite linen jogger RW", "Iris petite linen jogger"),
    ("Cool Show earring pk", "Cool Show earring"),
    ("Bahamas jumpsuit(1)", "Bahamas jumpsuit"),
    ("OSLO MINI BOBBLE NEW", "Oslo Mini Bobble"),
    ("PQ BOSTON SILK SKIRT", "Boston Silk Skirt"),
    ("Katalin PQ sandalette", "Katalin sandalette"),
    ("Trucker Jacket(1)", "Trucker Jacket"),
    ("vermont fancy slacks (1)", "vermont fancy slacks"),
    ("New Yoko espadrille", "Yoko espadrille"),
    ("Lily NEW SL (W)", "Lily SL (W)"),
    ("Basic 9-pk studs", "Basic 9-pack studs"),
    ("MARIO 2PK REG TAPERED TRS", "Mario 2-pack Reg Tapered"),
    ("OL CLEVELAND PQ", "OL Cleveland"),
    ("Zoe PQ sandalette(1)", "Zoe sandalette"),
    ("Maxi tansitional boot", "Maxi transitional boot"),
    ("Flirty Mysan ring pk.", "Flirty Mysan ring"),
    ("TILDA THIN BELT NEW", "Tilda Thin Belt"),
    ("SPEED BRUNO  SHIRT", "Speed Bruno Shirt"),
    ("SUPREME RW tights", "Supreme tights"),
    ("PQ RIGA TENCEL TIGHTS", "Riga Tencel Tights"),
    ("R-NECK SS BASIC 3 PK", "R-Neck SS Basic 3-pack"),
    ("CLAY TEE", "Clay Tee"),
    ("Beetle beanie new", "Beetle beanie"),
    ("TD Hugo slim fit Stretch PQ", "Hugo slim fit Stretch"),
    ("Laurent slim fit jogger old", "Laurent slim fit jogger"),
    ("Jamie Skinny Cropped Trs Pid", "Jamie Skinny Cropped"),
    ("Wave Linen Relaxed fit PID", "Wave Linen Relaxed fit"),
]


@pytest.mark.parametrize(("raw", "shown"), REAL_NAMES)
def test_real_names_are_cleaned_for_display(raw, shown):
    assert display_name(raw) == shown


def test_clean_names_stay_and_a_name_is_never_emptied():
    for name in ("Paloma", "Chunky Knit Sweater", "Hedi wrap dress", "Jeff AOP"):
        assert display_name(name) == name
    assert display_name("NEW") == "NEW"
    assert display_name("  Nora   dress (1) ") == "Nora dress"


def test_products_keep_the_raw_name_and_add_the_display_name():
    product = Product(
        product_id=1, name="PQ BOSTON SILK SKIRT", product_type="Skirt",
        product_group="Garment Lower body", section="Ladieswear", colour="Black",
        description="A skirt.", price=29.99,
    )  # fmt: skip
    assert product.name == "PQ BOSTON SILK SKIRT"
    assert product.display_name == "Boston Silk Skirt"
    dumped = product.model_dump()
    assert (dumped["name"], dumped["display_name"]) == ("PQ BOSTON SILK SKIRT", "Boston Silk Skirt")
    assert Product.model_validate(dumped) == product  # a client may send it back
