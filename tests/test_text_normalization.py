"""Critical bugs #2 (prompt corruption) and #3 (substring matching)."""

import gzip
import random

import pytest

from orchestration.agents.story.patterns import DEFAULT_LIBRARY
from services.prompt_engine.engine import IntentEngine
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from services.prompt_engine.normalizer import WORDLIST_PATH, correct_token, is_english_word, normalize
from shared.contracts.vocab import Genre
from shared.text import inflection_bases, phrase_in, phrases_in, tokenize
from tests.conftest import make_clip

REVIEW_CASES = ["ride", "pride", "storm", "hope", "slot", "gold", "load", "mild", "sloe"]


@pytest.mark.parametrize("word", REVIEW_CASES)
def test_review_corruptions_are_fixed(word):
    assert correct_token(word) is None
    assert word in normalize(f"a {word} video").tokens


def test_no_english_word_or_inflection_is_ever_corrected():
    with gzip.open(WORDLIST_PATH, "rt") as f:
        words = [w.strip() for w in f if w.strip()]
    rng = random.Random(7)
    sample = rng.sample(words, 20000)
    regular = [w for w in rng.sample(words, 6000) if len(w) > 3 and w[-1] not in "sxzye" and not w.endswith(("ch", "sh"))]
    inflected = [w + suffix for w in regular[:3000] for suffix in ("s", "ed", "ing")]
    changed = {w: correct_token(w) for w in sample + inflected if correct_token(w) is not None}
    assert not changed, f"valid English words were rewritten: {dict(list(changed.items())[:10])}"


@pytest.mark.parametrize("typo,fix", [("fsat", "fast"), ("agressive", "aggressive"), ("motivaton", "motivation"),
                                       ("nkie", "nike"), ("slwo", "slow"), ("cinamatic", "cinematic"), ("podcsat", "podcast")])
def test_real_typos_still_corrected(typo, fix):
    assert correct_token(typo) == fix


def test_is_english_word_handles_inflections():
    assert all(is_english_word(w) for w in ("rides", "hoped", "storms", "slotted", "happily"))
    assert not is_english_word("fsat")
    assert "ride" in inflection_bases("rides") and "hope" in inflection_bases("hoped")


def test_phrase_matching_is_token_bounded():
    assert not phrase_in(tokenize("a pretty product video"), "pr")
    assert not phrase_in(tokenize("made for my shadow puppet"), "ad")
    assert not phrase_in(tokenize("quiet reflection"), "flex")
    assert phrase_in(tokenize("three heavy deadlifts"), "deadlift")
    assert phrase_in(tokenize("before and after shots"), "before and after")
    assert phrases_in("leg day at the gym", ["leg day", "gym", "yoga"]) == ["leg day", "gym"]


def test_pattern_keywords_no_substring_false_positives():
    prompt = LexicalIntentExtractor().extract("a pretty product video made for my shadow puppet show").normalized_prompt
    tokens = tokenize(prompt)
    hits = {p.pattern_id: phrases_in(tokens, p.keywords) for p in DEFAULT_LIBRARY}
    assert "pr" not in hits["fitness_reel"]
    assert "ad" not in hits["product_launch"]


def test_footage_cues_no_substring_false_positives():
    intent = LexicalIntentExtractor().extract("make it cool")
    clips = [make_clip(f"c{i}", 5, activities=("quiet reflection in a courtyard",)) for i in range(4)]
    assert IntentEngine().reconcile_with_footage(intent, clips).genre.is_default


def test_pride_parade_is_not_a_wedding():
    a = LexicalIntentExtractor().extract("pride parade ride through the city")
    assert a.genre.value != Genre.WEDDING
    assert not a.corrections
