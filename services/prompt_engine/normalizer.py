"""
MONTA — Prompt Normalizer
===========================
Turns messy creator text into a clean token stream:

* Unicode/case normalization and symbol expansion ("b&w", "w/").
* Clause boundaries from punctuation and connectives ("then", "but").
* Slang expansion ("insta" → "instagram", "gonna" → "going to").
* Conservative spelling correction toward the domain vocabulary using
  optimal-string-alignment distance (handles transpositions like "nkie").
  A token that is a valid English word — or an inflection of one — is never
  corrected: ``ride`` stays ``ride`` even though ``bride`` is one edit away.
  The protected list (``data/english_words.txt.gz``, ~210k words) is Webster's
  Second International (public domain) plus modern creator vocabulary.

Every change is recorded as a ``Correction`` so the reasoning trace shows
exactly how the prompt was interpreted.
"""

import gzip
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from services.prompt_engine.lexicon import CLAUSE_BREAKERS, COMMON_WORDS, SLANG, vocabulary
from shared.contracts.intent import Correction
from shared.text import inflection_bases

WORDLIST_PATH = Path(__file__).parent / "data" / "english_words.txt.gz"

BOUNDARY = "<b>"
MAX_PROMPT_CHARS = 4000

_PRE_REPLACEMENTS = [
    (re.compile(r"\bb\s*&\s*w\b"), " black and white "),
    (re.compile(r"\bw/(?=\s|$)"), " with "),
    (re.compile(r"\bw/o\b"), " without "),
    (re.compile(r"&"), " and "),
    (re.compile(r"\+"), " and "),
    (re.compile(r"[-_/]+"), " "),
]
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[.,;:!?\n]")


def osa_distance(a: str, b: str, limit: int = 3) -> int:
    """Optimal string alignment distance (Levenshtein + adjacent transposition), capped at ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


@lru_cache(maxsize=1)
def _vocab() -> frozenset[str]:
    return frozenset(vocabulary())


@lru_cache(maxsize=1)
def english_words() -> frozenset[str]:
    with gzip.open(WORDLIST_PATH, "rt", encoding="utf-8") as f:
        return frozenset(line.strip() for line in f if line.strip())


def is_english_word(token: str) -> bool:
    """True for dictionary words and their regular inflections (rides, hoped, storms, slotted)."""
    words = english_words()
    return any(base in words for base in inflection_bases(token))


@lru_cache(maxsize=65536)
def correct_token(token: str) -> str | None:
    """Return a vocabulary correction for ``token`` or None when it should be left alone."""
    vocab = _vocab()
    if token in vocab or token in COMMON_WORDS or not token.isalpha() or len(token) < 4:
        return None
    if token.endswith("s") and token[:-1] in vocab:
        return None
    if is_english_word(token):
        return None
    max_dist = 1 if len(token) <= 6 else 2
    best: list[tuple[int, str]] = []
    for word in vocab:
        if abs(len(word) - len(token)) > max_dist:
            continue
        d = osa_distance(token, word, max_dist)
        if d <= max_dist:
            best.append((d, word))
    if not best:
        return None
    best.sort()
    top_d = best[0][0]
    tied = [w for d, w in best if d == top_d]
    if len(tied) > 1:
        same_initial = [w for w in tied if w[0] == token[0]]
        if len(same_initial) != 1:
            return None
        return same_initial[0]
    return tied[0]


@dataclass
class NormalizedPrompt:
    raw: str
    tokens: list[str]
    corrections: list[Correction] = field(default_factory=list)
    corrected_indices: set[int] = field(default_factory=set)
    truncated: bool = False

    @property
    def text(self) -> str:
        return " ".join(t if t != BOUNDARY else "|" for t in self.tokens)

    @property
    def content_tokens(self) -> list[str]:
        return [t for t in self.tokens if t != BOUNDARY]


def normalize(raw: str) -> NormalizedPrompt:
    """Normalize a raw prompt. Never raises on user text."""
    truncated = len(raw) > MAX_PROMPT_CHARS
    text = unicodedata.normalize("NFKC", raw[:MAX_PROMPT_CHARS]).lower()
    text = text.replace("’", "'").replace("‘", "'")
    for pattern, replacement in _PRE_REPLACEMENTS:
        text = pattern.sub(replacement, text)

    corrections: list[Correction] = []
    corrected: set[int] = set()
    tokens: list[str] = []
    for raw_tok in _TOKEN.findall(text):
        if not raw_tok[0].isalnum():
            if tokens and tokens[-1] != BOUNDARY:
                tokens.append(BOUNDARY)
            continue
        if raw_tok in SLANG:
            expansion = SLANG[raw_tok]
            if expansion != raw_tok:
                corrections.append(Correction(original=raw_tok, corrected=expansion or "∅", kind="slang"))
            tokens.extend(expansion.split())
            continue
        fixed = correct_token(raw_tok)
        if fixed is not None:
            corrections.append(Correction(original=raw_tok, corrected=fixed, kind="spelling"))
            corrected.add(len(tokens))
            tokens.append(fixed)
            continue
        if raw_tok in CLAUSE_BREAKERS:
            if tokens and tokens[-1] != BOUNDARY:
                tokens.append(BOUNDARY)
            continue
        tokens.append(raw_tok)

    while tokens and tokens[-1] == BOUNDARY:
        tokens.pop()
    return NormalizedPrompt(raw=raw, tokens=tokens, corrections=corrections, corrected_indices=corrected, truncated=truncated)
