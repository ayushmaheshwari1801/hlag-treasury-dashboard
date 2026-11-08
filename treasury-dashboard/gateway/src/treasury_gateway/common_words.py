"""Ordinary English words for the recommendation name check.

A capitalised word at the start of a sentence is skipped only when its lowercase form is an ordinary English word
("Review the reserve"); any other capitalised word is checked like a name ("Maersk raised its rates"). The list is
the 30,000 most frequent English words from wordfreq (data/english_words_30k.txt, CC BY-SA 4.0; see
data/NOTICE-english_words_30k.md). Plural, -ed, -ing and -er forms are matched by the helper below.

Limitation: a frequency list also contains frequent names (e.g. "london", "google"). Such a name is not checked when
it is the very first word of a sentence; anywhere else it is always checked.
"""
from functools import lru_cache
from pathlib import Path

WORDS_FILE = Path(__file__).resolve().parent / "data" / "english_words_30k.txt"


@lru_cache(maxsize=1)
def words() -> frozenset[str]:
    return frozenset(w.strip() for w in WORDS_FILE.read_text(encoding="utf-8").splitlines() if w.strip())


def ordinary(word: str) -> bool:
    """True when the lowercase word (or its simple plural / -ed / -ing / -er form) is an ordinary English word."""
    w = word.casefold().strip("'’.,;:")
    wl = words()
    if w in wl:
        return True
    for suffix, add in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"),
                        ("ers", ""), ("er", ""), ("ly", "")):
        if w.endswith(suffix) and w[: -len(suffix)] + add in wl:
            return True
    return False
