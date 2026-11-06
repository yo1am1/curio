"""Display names for H&M products: the raw `name` stays in the data and IDs; the UI and the
agents' text show `display_name(name)`.

H&M names carry merchandising codes ("PQ" premium quality, "RW", "TD", "Pid"), stock notes
("new", "old", "(1)"), pack abbreviations ("pk", "2PK"), a typo ("tansitional") and capitals
("PQ BOSTON SILK SKIRT"). The cleaner drops the codes and notes, spells packs out, fixes the
typo, title-cases shouty words and collapses spaces. It never returns an empty name.
"""

import re

# Whole tokens that are codes or stock notes, never part of what the piece is.
DROPPED = frozenset({"new", "old", "pk", "pk.", "rw", "trs", "pq", "td", "pid", "pq/pu", "pu/pq"})
_COPY = re.compile(r"\s*\(\d+\)")  # "Nora dress(1)", "Asta Dress (1)"
_PACK = re.compile(r"\b(\d+)\s*-?\s*pk\b\.?", re.IGNORECASE)  # "2PK", "9-pk", "3 PK"
_TYPOS = {"tansitional": "transitional"}
_TOKEN = re.compile(r"\S+")
_LETTERS = re.compile(r"[A-Za-z]")


def display_name(raw: str) -> str:
    """ "PQ BOSTON SILK SKIRT" -> "Boston Silk Skirt", "Flirty Mysan ring pk." -> "Flirty
    Mysan ring", "Nora dress(1)" -> "Nora dress", "MARIO 2PK REG TAPERED TRS" -> "Mario
    2-pack Reg Tapered", "Maxi tansitional boot" -> "Maxi transitional boot"."""
    text = _COPY.sub(" ", raw)
    kept = [w for w in _TOKEN.findall(_PACK.sub(" ", text)) if w.casefold() not in DROPPED]
    letters = "".join(_LETTERS.findall(" ".join(kept)))
    shouty = len(letters) >= 4 and letters.isupper()
    text = _PACK.sub(lambda m: f"{m.group(1)}-pack", text)
    words = [w for w in _TOKEN.findall(text) if w.casefold() not in DROPPED]
    words = [_TYPOS.get(w.casefold(), w) for w in words]
    cleaned = " ".join(_quiet(w, shouty) for w in words).strip(" -/,")
    return cleaned or " ".join(raw.split())


def _quiet(word: str, shouty: bool) -> str:
    """A shouted word in title case: every word of a shouty name longer than two letters
    ("TEE" -> "Tee", "R-NECK" -> "R-Neck"; "SS" stays), and in other names words of four
    letters or more ("SUPREME"); short codes in a mixed name stay ("AOP", "HW")."""
    letters = "".join(_LETTERS.findall(word))
    if not letters.isupper() or len(letters) <= (2 if shouty else 3):
        return word
    return "-".join(part[:1].upper() + part[1:].lower() for part in word.split("-"))
