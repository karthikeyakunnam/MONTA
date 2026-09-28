"""
MONTA — Token & Phrase Matching
=================================
Every keyword/cue lookup in Layers 3–7 goes through here. Matching is on
token boundaries only: ``"pr"`` never matches inside ``"pretty"``, ``"flex"``
never matches inside ``"reflection"``. Light inflection tolerance lets
``"deadlifts"``/``"lifting"`` match cue ``"deadlift"``/``"lift"`` without
substring overlap.
"""

import re
from collections.abc import Iterable
from functools import lru_cache

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@lru_cache(maxsize=65536)
def inflection_bases(token: str) -> frozenset[str]:
    """Plausible base forms of an inflected token (the token itself included)."""
    out = {token}
    if len(token) <= 3:
        return frozenset(out)
    if token.endswith("ies") and len(token) > 4:
        out.add(token[:-3] + "y")
    if token.endswith("es"):
        out.add(token[:-2])
    if token.endswith("s") and not token.endswith("ss"):
        out.add(token[:-1])
    if token.endswith("ied"):
        out.add(token[:-3] + "y")
    for suffix in ("ed", "ing", "er", "ers", "est", "ly"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            stem = token[: -len(suffix)]
            out.add(stem)
            out.add(stem + "e")
            if len(stem) >= 2 and stem[-1] == stem[-2]:
                out.add(stem[:-1])
    return frozenset(out)


def _token_matches(token: str, cue: str) -> bool:
    return token == cue or cue in inflection_bases(token)


def phrase_in(tokens: list[str], phrase: str) -> bool:
    """True if ``phrase`` occurs as a contiguous token sequence; the last token may be inflected."""
    parts = phrase.lower().split()
    n = len(parts)
    if n == 0 or n > len(tokens):
        return False
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n - 1] == parts[:-1] and _token_matches(tokens[i + n - 1], parts[-1]):
            return True
    return False


def phrases_in(text: str | list[str], phrases: Iterable[str]) -> list[str]:
    """All ``phrases`` present in ``text`` on token boundaries, in input order."""
    tokens = tokenize(text) if isinstance(text, str) else text
    return [p for p in phrases if phrase_in(tokens, p)]
