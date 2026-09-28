"""
MONTA — Prompt Lexicon
========================
Curated vocabulary mapping creator language to controlled values.

Each cue maps a phrase (already normalized: lowercase, space-separated tokens)
to one or more ``(dimension, value, weight)`` signals. Weights are relative
evidence strengths: 1.0 is an explicit, unambiguous mention ("slow"), 0.3–0.6
is suggestive ("epic" hints at dramatic), >1.0 is a precise multi-word phrase
("aggressive cuts").

Brand/reference cues ("nike", "movie trailer") expand into several signals
because a reference implies a whole style.

Production note: this table is data, versioned with ``LEXICON_VERSION``. It is
the deterministic floor under the LLM extractor and the source of span-level
evidence; extend it from the ``corrections``/``missing`` telemetry of real
prompts rather than by intuition.
"""

LEXICON_VERSION = "lexicon.v1"

Signal = tuple[str, str, float]

GENRE, EMOTION, PACE, PLATFORM, COLOR, CAPTION, MUSIC, REFERENCE = (
    "genre", "emotion", "pace", "target_platform", "color_grade", "caption_style", "music_style", "reference_style",
)


def _many(dimension: str, value: str, phrases: dict[str, float]) -> dict[str, list[Signal]]:
    return {p: [(dimension, value, w)] for p, w in phrases.items()}


_TABLES: list[dict[str, list[Signal]]] = [
    # ---------------- genre ----------------
    _many(GENRE, "fitness", {
        "gym": 1.0, "workout": 1.0, "lifting": 1.0, "lift": 0.8, "deadlift": 1.0, "squat": 1.0, "bench press": 1.0,
        "gains": 0.9, "bodybuilding": 1.0, "physique": 1.0, "fitness": 1.0, "crossfit": 1.0, "cardio": 0.8,
        "training": 0.6, "transformation": 0.5, "bulk": 0.7, "calisthenics": 1.0, "powerlifting": 1.0,
    }),
    _many(GENRE, "sports", {
        "sports": 1.0, "sport": 1.0, "football": 1.0, "soccer": 1.0, "basketball": 1.0, "nba": 1.0, "tennis": 1.0,
        "boxing": 0.8, "mma": 1.0, "athlete": 0.9, "match": 0.6, "game day": 1.0, "highlights": 0.6, "stadium": 0.7,
        "race": 0.6, "marathon": 0.9, "skateboarding": 1.0, "surfing": 0.8, "cricket": 1.0,
    }),
    _many(GENRE, "travel", {
        "travel": 1.0, "trip": 1.0, "vacation": 1.0, "holiday": 0.9, "journey": 0.5, "explore": 0.7, "exploring": 0.7,
        "adventure": 0.7, "road trip": 1.2, "roadtrip": 1.2, "beach": 0.6, "backpacking": 1.0, "destination": 0.8, "tour": 0.6,
    }),
    _many(GENRE, "luxury", {
        "luxury": 1.0, "luxurious": 0.8, "yacht": 1.0, "supercar": 1.0, "lambo": 1.0, "penthouse": 1.0, "premium": 0.6,
        "mansion": 1.0, "private jet": 1.2,
    }),
    _many(GENRE, "fashion", {
        "fashion": 1.0, "outfit": 1.0, "outfits": 1.0, "ootd": 1.0, "runway": 1.0, "lookbook": 1.0, "streetwear": 1.0,
        "photoshoot": 0.7, "model": 0.4,
    }),
    _many(GENRE, "lifestyle", {
        "lifestyle": 1.0, "day in my life": 1.4, "day in the life": 1.4, "routine": 0.8, "vlog": 0.8, "daily": 0.5,
        "morning routine": 1.2,
    }),
    _many(GENRE, "business", {
        "business": 1.0, "startup": 1.0, "company": 0.7, "corporate": 1.0, "office": 0.6, "entrepreneur": 1.0,
        "founder": 0.9, "pitch": 0.7, "brand story": 1.0,
    }),
    _many(GENRE, "education", {
        "tutorial": 1.2, "how to": 1.0, "howto": 1.0, "lesson": 1.0, "explain": 0.8, "explainer": 1.0, "learn": 0.6,
        "course": 0.8, "teach": 0.8, "step by step": 1.0, "tips": 0.5, "guide": 0.5,
    }),
    _many(GENRE, "cinematic", {"cinematic": 0.6, "film": 0.5, "movie": 0.5, "filmic": 0.6, "short film": 1.0, "montage": 0.5}),
    _many(GENRE, "documentary", {
        "documentary": 1.2, "docu": 1.0, "behind the scenes": 1.0, "bts": 1.0, "real story": 0.8, "mini doc": 1.2,
    }),
    _many(GENRE, "event", {
        "event": 1.0, "concert": 1.0, "festival": 1.0, "party": 0.9, "conference": 1.0, "gig": 1.0, "birthday": 0.8,
        "graduation": 1.0, "club": 0.5, "recap": 0.6, "aftermovie": 1.2,
    }),
    _many(GENRE, "wedding", {
        "wedding": 1.2, "bride": 1.0, "groom": 1.0, "vows": 1.0, "ceremony": 0.6, "reception": 0.5, "engagement": 0.6,
        "marriage": 0.8, "honeymoon": 0.6,
    }),
    _many(GENRE, "product", {
        "product": 1.0, "unboxing": 1.0, "product launch": 1.4, "launch": 0.6, "promo": 0.6, "review": 0.5,
        "gadget": 0.8, "drop": 0.3, "showcase": 0.5,
    }),
    _many(GENRE, "podcast", {
        "podcast": 1.2, "episode": 0.7, "interview": 0.6, "talking head": 1.0, "conversation": 0.5, "clips from": 0.3,
    }),
    # ---------------- emotion ----------------
    _many(EMOTION, "motivational", {
        "motivation": 1.0, "motivational": 1.0, "motivate": 1.0, "motivating": 1.0, "grind": 0.9, "discipline": 0.9,
        "never give up": 1.3, "hustle": 0.8, "determination": 0.9, "no excuses": 1.1,
    }),
    _many(EMOTION, "inspiring", {
        "inspiring": 1.0, "inspire": 1.0, "inspirational": 1.0, "legendary": 0.6, "greatness": 0.9, "dream": 0.5,
        "hopeful": 0.8,
    }),
    _many(EMOTION, "emotional", {
        "emotional": 1.0, "touching": 1.0, "heartfelt": 1.0, "tearjerker": 1.2, "sad": 0.9, "cry": 0.8, "feels": 0.6,
        "moving": 0.5, "sentimental": 0.9,
    }),
    _many(EMOTION, "intense", {"intense": 1.0, "intensity": 1.0, "raw": 0.7, "gritty": 0.8, "hardcore": 0.9, "brutal": 0.8}),
    _many(EMOTION, "energetic", {
        "energetic": 1.0, "energy": 0.8, "hype": 1.0, "hyped": 1.0, "lit": 0.7, "fire": 0.5, "pumped": 1.0, "upbeat": 0.8,
        "banger": 0.6, "exciting": 0.8,
    }),
    _many(EMOTION, "dramatic", {
        "dramatic": 1.0, "drama": 0.8, "epic": 0.6, "moody": 0.5, "suspense": 1.0, "tension": 0.8, "powerful": 0.5,
    }),
    _many(EMOTION, "luxury", {"classy": 1.0, "elegant": 0.9, "sophisticated": 1.0, "sleek": 0.7, "luxurious": 0.5}),
    _many(EMOTION, "uplifting", {
        "uplifting": 1.0, "happy": 0.8, "positive": 0.8, "feel good": 1.0, "feelgood": 1.0, "cheerful": 1.0, "wholesome": 1.0,
    }),
    _many(EMOTION, "aggressive", {
        "aggressive": 0.7, "savage": 1.0, "beast": 0.8, "beast mode": 1.2, "angry": 0.9, "rage": 1.0, "menacing": 1.0,
    }),
    _many(EMOTION, "calm", {
        "calm": 1.0, "chill": 0.8, "relaxing": 1.0, "relaxed": 0.9, "peaceful": 1.0, "serene": 1.0, "soft": 0.6,
        "gentle": 0.7, "cozy": 0.7, "dreamy": 0.6, "aesthetic": 0.4,
    }),
    _many(EMOTION, "romantic", {"romantic": 1.0, "love": 0.7, "couple": 0.6, "intimate": 0.8, "love story": 1.2}),
    _many(EMOTION, "joyful", {"joyful": 1.0, "fun": 0.8, "playful": 1.0, "joy": 1.0, "celebrate": 0.8, "celebration": 0.6, "excited": 0.6}),
    _many(EMOTION, "nostalgic", {"nostalgic": 1.0, "nostalgia": 1.0, "memories": 0.9, "throwback": 1.0, "remember": 0.5}),
    # ---------------- pace ----------------
    _many(PACE, "slow", {
        "slow": 1.0, "slowly": 1.0, "slow mo": 0.8, "slowmo": 0.8, "slow motion": 0.8, "take its time": 1.0,
        "breathe": 0.5, "calm": 0.4, "chill": 0.3, "gentle": 0.3, "relaxing": 0.3,
    }),
    _many(PACE, "medium", {"medium pace": 1.2, "balanced": 0.7, "moderate": 0.9, "steady": 0.8, "normal pace": 1.0}),
    _many(PACE, "fast", {
        "fast": 1.0, "quick": 0.9, "quickly": 0.8, "snappy": 1.0, "rapid": 1.0, "fast paced": 1.3, "fast cuts": 1.3,
        "quick cuts": 1.3, "upbeat": 0.4, "energetic": 0.4, "dynamic": 0.6,
    }),
    _many(PACE, "aggressive", {
        "aggressive cuts": 1.5, "aggressive editing": 1.5, "hard cuts": 1.3, "rapid cuts": 1.4, "hard hitting": 1.1,
        "frantic": 1.0, "cut on every beat": 1.3, "every beat": 0.9, "aggressive": 0.6, "hype": 0.4, "jump cuts": 0.8,
    }),
    # ---------------- platform ----------------
    _many(PLATFORM, "instagram", {"instagram": 1.2, "reel": 1.0, "reels": 1.0, "insta reel": 1.4}),
    _many(PLATFORM, "tiktok", {"tiktok": 1.2, "tik tok": 1.2, "fyp": 0.8}),
    _many(PLATFORM, "youtube_short", {"shorts": 1.0, "youtube short": 1.4, "youtube shorts": 1.4}),
    _many(PLATFORM, "youtube", {"youtube": 0.9, "youtube video": 1.2, "long form": 1.0, "longform": 1.0}),
    _many(PLATFORM, "general", {"website": 1.0, "landing page": 1.0, "presentation": 0.9, "tv": 0.8, "broadcast": 1.0}),
    # ---------------- color ----------------
    _many(COLOR, "dark", {
        "dark": 1.0, "dark colors": 1.5, "dark colours": 1.5, "dark tones": 1.5, "dark grade": 1.5, "low key": 1.0,
        "gloomy": 0.8, "noir": 0.6, "moody": 0.5,
    }),
    _many(COLOR, "orange_teal", {
        "orange teal": 1.5, "teal orange": 1.5, "orange and teal": 1.5, "teal and orange": 1.5, "hollywood look": 1.0,
        "blockbuster": 0.5,
    }),
    _many(COLOR, "bw", {"black and white": 1.5, "bw": 1.2, "monochrome": 1.3, "grayscale": 1.3, "greyscale": 1.3}),
    _many(COLOR, "vibrant", {"vibrant": 1.2, "colorful": 1.2, "colourful": 1.2, "saturated": 1.0, "bright colors": 1.3, "poppy": 0.9}),
    _many(COLOR, "natural", {"natural colors": 1.3, "natural look": 1.3, "true to life": 1.2, "realistic": 0.7, "no filter": 1.0}),
    _many(COLOR, "vintage", {"vintage": 1.0, "retro": 0.9, "film look": 1.0, "faded": 0.8, "old school": 0.7, "film grain": 0.8}),
    _many(COLOR, "neon", {"neon": 1.2, "cyberpunk": 1.0, "synthwave": 1.0}),
    _many(COLOR, "warm", {"warm": 0.9, "warm tones": 1.3, "golden": 0.8, "golden hour": 1.1, "sunset": 0.4}),
    _many(COLOR, "cool", {"cool tones": 1.3, "cold colors": 1.3, "blue tones": 1.2, "icy": 1.0}),
    _many(COLOR, "high_contrast", {"high contrast": 1.5, "contrasty": 1.2, "punchy": 0.8, "crushed blacks": 1.2}),
    # ---------------- captions ----------------
    _many(CAPTION, "Gymshark", {"gymshark captions": 1.5}),
    _many(CAPTION, "David Goggins", {"stay hard": 1.0}),
    _many(CAPTION, "Cinematic Documentary", {"documentary captions": 1.5, "subtitles": 0.6}),
    {
        p: [(CAPTION, "Motivational", w), (EMOTION, "motivational", 0.6)]
        for p, w in {
            "motivational captions": 1.6, "motivation captions": 1.5, "motivational quotes": 1.5,
            "motivation quotes": 1.4, "motivational text": 1.5,
        }.items()
    },
    _many(CAPTION, "Motivational", {"quotes": 0.6}),
    _many(CAPTION, "Alpha Mindset", {"alpha": 1.0, "sigma": 1.0, "alpha mindset": 1.5}),
    _many(CAPTION, "Minimal", {"minimal captions": 1.5, "minimal text": 1.5, "clean text": 1.2, "minimal": 0.5}),
    _many(CAPTION, "None", {"no captions": 1.6, "no text": 1.5, "without captions": 1.6, "no subtitles": 1.5}),
    # ---------------- music ----------------
    _many(MUSIC, "epic orchestral", {"orchestral": 1.2, "orchestra": 1.0, "epic music": 1.2, "symphonic": 1.0, "hans zimmer": 1.3}),
    _many(MUSIC, "hard trap", {"trap": 1.0, "hard trap": 1.4, "808": 0.9, "heavy bass": 1.1, "bass": 0.5, "drill": 0.9, "phonk": 1.0}),
    _many(MUSIC, "cinematic trailer", {"trailer music": 1.4}),
    _many(MUSIC, "emotional piano", {"piano": 1.1, "sad piano": 1.4, "emotional piano": 1.4}),
    _many(MUSIC, "hybrid cinematic", {"hybrid": 0.8, "cinematic music": 1.2, "cinematic beat": 1.2}),
    _many(MUSIC, "sports anthem", {"anthem": 1.0, "rock anthem": 1.3, "rock": 0.6}),
    _many(MUSIC, "lo-fi", {"lofi": 1.3, "lo fi": 1.3}),
    _many(MUSIC, "acoustic", {"acoustic": 1.2, "guitar": 0.8}),
    _many(MUSIC, "electronic", {"edm": 1.2, "electronic": 1.0, "techno": 1.0, "house music": 1.2, "dubstep": 1.0}),
    _many(MUSIC, "ambient", {"ambient": 1.1, "atmospheric": 0.9, "soft music": 1.0}),
]

# Named references imply a whole style.
REFERENCES: dict[str, list[Signal]] = {
    "nike": [(REFERENCE, "nike", 1.2), (GENRE, "sports", 0.8), (GENRE, "fitness", 0.4), (EMOTION, "inspiring", 1.0),
             (EMOTION, "motivational", 0.5), (CAPTION, "Nike", 1.5), (MUSIC, "hybrid cinematic", 0.5)],
    "adidas": [(REFERENCE, "adidas", 1.2), (GENRE, "sports", 0.8), (EMOTION, "inspiring", 0.8), (EMOTION, "energetic", 0.4)],
    "gymshark": [(REFERENCE, "gymshark", 1.2), (GENRE, "fitness", 1.0), (EMOTION, "energetic", 0.8), (CAPTION, "Gymshark", 1.5),
                 (MUSIC, "hard trap", 0.6), (PACE, "fast", 0.5)],
    "apple": [(REFERENCE, "apple", 1.0), (GENRE, "product", 1.0), (EMOTION, "luxury", 0.5), (EMOTION, "calm", 0.4),
              (CAPTION, "Minimal", 1.0), (MUSIC, "electronic", 0.4), (PACE, "medium", 0.4)],
    "red bull": [(REFERENCE, "red bull", 1.2), (GENRE, "sports", 0.9), (EMOTION, "energetic", 1.0), (PACE, "fast", 0.8),
                 (MUSIC, "electronic", 0.5)],
    "redbull": [(REFERENCE, "red bull", 1.2), (GENRE, "sports", 0.9), (EMOTION, "energetic", 1.0), (PACE, "fast", 0.8)],
    "goggins": [(REFERENCE, "goggins", 1.2), (EMOTION, "motivational", 1.0), (EMOTION, "aggressive", 0.6),
                (CAPTION, "David Goggins", 1.5), (GENRE, "fitness", 0.5), (COLOR, "dark", 0.4)],
    "david goggins": [(REFERENCE, "goggins", 1.4), (EMOTION, "motivational", 1.0), (EMOTION, "aggressive", 0.6),
                      (CAPTION, "David Goggins", 1.6), (GENRE, "fitness", 0.5)],
    "movie trailer": [(REFERENCE, "movie trailer", 1.4), (GENRE, "cinematic", 0.8), (EMOTION, "dramatic", 1.0),
                      (MUSIC, "cinematic trailer", 1.3), (PACE, "fast", 0.4)],
    "trailer": [(REFERENCE, "movie trailer", 1.0), (GENRE, "cinematic", 0.6), (EMOTION, "dramatic", 0.8),
                (MUSIC, "cinematic trailer", 1.0)],
    "netflix": [(REFERENCE, "netflix documentary", 1.0), (GENRE, "documentary", 0.9), (EMOTION, "dramatic", 0.5),
                (CAPTION, "Cinematic Documentary", 1.2)],
    "a24": [(REFERENCE, "a24", 1.2), (GENRE, "cinematic", 1.0), (EMOTION, "dramatic", 0.6), (PACE, "slow", 0.6),
            (COLOR, "natural", 0.4)],
    "wes anderson": [(REFERENCE, "wes anderson", 1.3), (GENRE, "cinematic", 0.8), (EMOTION, "joyful", 0.4),
                     (COLOR, "vibrant", 0.7), (PACE, "medium", 0.5)],
    "mrbeast": [(REFERENCE, "mrbeast", 1.2), (GENRE, "lifestyle", 0.5), (EMOTION, "energetic", 1.0), (PACE, "aggressive", 0.8)],
    "mr beast": [(REFERENCE, "mrbeast", 1.2), (GENRE, "lifestyle", 0.5), (EMOTION, "energetic", 1.0), (PACE, "aggressive", 0.8)],
    "rolex": [(REFERENCE, "rolex", 1.2), (GENRE, "luxury", 1.2), (EMOTION, "luxury", 0.8), (PACE, "slow", 0.5), (COLOR, "dark", 0.3)],
    "casey neistat": [(REFERENCE, "casey neistat", 1.3), (GENRE, "lifestyle", 1.0), (EMOTION, "energetic", 0.5), (PACE, "fast", 0.6)],
    "ad": [(REFERENCE, "commercial", 0.5), (GENRE, "product", 0.3), (EMOTION, "inspiring", 0.3)],
    "commercial": [(REFERENCE, "commercial", 0.8), (GENRE, "product", 0.4), (EMOTION, "inspiring", 0.3)],
}


def build_cues() -> dict[str, list[Signal]]:
    cues: dict[str, list[Signal]] = {}
    for table in _TABLES:
        for phrase, signals in table.items():
            cues.setdefault(phrase, []).extend(signals)
    for phrase, signals in REFERENCES.items():
        cues.setdefault(phrase, []).extend(signals)
    return cues


CUES: dict[str, list[Signal]] = build_cues()
MAX_PHRASE_TOKENS = max(len(p.split()) for p in CUES)

# ---------------- structural vocabulary ----------------

SECTION_WORDS: dict[str, str] = {
    **dict.fromkeys(["start", "starts", "starting", "beginning", "begin", "begins", "intro", "opening", "open", "opener", "first"], "start"),
    **dict.fromkeys(["middle", "mid", "midway", "halfway"], "middle"),
    **dict.fromkeys(["end", "ending", "ends", "finale", "outro", "climax", "last", "finish", "final", "closing"], "end"),
}

INTENSIFIERS = {
    "very", "super", "really", "extremely", "huge", "massive", "insanely", "insane", "hella", "mad", "crazy", "ultra",
    "max", "maximum", "big", "full", "totally", "so", "extra",
}
POSTFIX_INTENSIFIERS = {"af", "asf"}
NEGATORS = {"not", "no", "dont", "don't", "never", "without", "avoid", "less", "nothing", "nope", "isnt", "isn't"}
CLAUSE_BREAKERS = {"then", "but", "while", "whereas", "afterwards", "after", "later"}

VAGUE_WORDS = {"cool", "nice", "good", "great", "amazing", "awesome", "something", "anything", "whatever", "better", "best", "sick", "dope"}

SLANG: dict[str, str] = {
    "vid": "video", "vids": "videos", "pls": "please", "plz": "please", "u": "you", "ur": "your", "rn": "right now",
    "gonna": "going to", "wanna": "want to", "kinda": "kind of", "sorta": "sort of", "abt": "about", "cuz": "because",
    "bc": "because", "insta": "instagram", "ig": "instagram", "yt": "youtube", "tt": "tiktok", "vibes": "vibe",
    "lowkey": "", "highkey": "very", "fr": "", "ngl": "", "bruh": "", "tho": "", "bday": "birthday", "goated": "legendary",
    "hyped": "hyped", "slowmo": "slow mo", "mins": "minutes", "min": "minutes", "secs": "seconds", "sec": "seconds",
    "colours": "colors", "colour": "color", "gud": "good", "gr8": "great", "fav": "favorite", "fave": "favorite",
    "b4": "before", "w": "with", "n": "and", "ya": "you", "yall": "you all", "thru": "through",
}

# Common words that must never be "spell-corrected" into lexicon terms.
COMMON_WORDS = set("""
a an the this that these those it its it's i me my mine you your yours we us our they them their he him his she her
make made making feel feels felt like likes then with and or but use using used want wants need needs video videos clip
clips show shows some more much very just also into onto from have has had give gives take takes look looks looking
keep put add over under only each every where when what which while after before about around part parts time times bit
lot lots kind sort type please thanks thank maybe should would could will can cant can't dont won't wont it all any
here there song songs music color colors style edit edits editing cut cuts cutting tone tones mood vibe good great nice
cool best better awesome amazing real something anything everything stuff thing things people person guy guys girl
girls friend friends family day days week year month today next story moment moments scene scenes shot shots footage
frame frames sure fit fits okay yeah yes into onto same other another first second third one two three four five ten
make also super feel mean means move moves moving make want know think love loved life live work works working
upload uploaded these those than such even still well back down side both most many few little long short high low
full part easy hard over again once twice make more some kind else ever never always often sometimes being been be
""".split())

COMPATIBLE_VALUES: dict[str, list[frozenset[str]]] = {
    EMOTION: [
        frozenset({"motivational", "inspiring"}), frozenset({"inspiring", "uplifting"}), frozenset({"energetic", "joyful"}),
        frozenset({"intense", "aggressive"}), frozenset({"dramatic", "intense"}), frozenset({"calm", "romantic"}),
        frozenset({"emotional", "nostalgic"}), frozenset({"emotional", "romantic"}), frozenset({"motivational", "intense"}),
    ],
    PACE: [frozenset({"fast", "aggressive"})],
    GENRE: [frozenset({"fitness", "sports"}), frozenset({"event", "wedding"}), frozenset({"product", "business"}),
            frozenset({"cinematic", "documentary"}), frozenset({"travel", "lifestyle"})],
}

# Pairs of (dimension, value) that contradict each other when both are asked for globally.
SOFT_CONFLICTS: list[tuple[tuple[str, str], tuple[str, str], str]] = [
    ((EMOTION, "calm"), (PACE, "aggressive"), "a calm mood is usually undermined by aggressive cutting"),
    ((EMOTION, "calm"), (PACE, "fast"), "a calm mood usually needs longer shots than fast pacing allows"),
    ((EMOTION, "romantic"), (PACE, "aggressive"), "romantic tone rarely survives aggressive cutting"),
    ((EMOTION, "luxury"), (PACE, "aggressive"), "luxury aesthetics rely on restraint; aggressive cuts cheapen them"),
    ((EMOTION, "emotional"), (MUSIC, "hard trap"), "hard trap tends to overpower an emotional tone"),
    ((EMOTION, "calm"), (MUSIC, "hard trap"), "hard trap contradicts a calm tone"),
    ((COLOR, "bw"), (COLOR, "vibrant"), "black & white cannot also be vibrant"),
]

CLARIFYING_QUESTIONS = {
    GENRE: "What kind of video is this — e.g. fitness, travel, event, product, wedding?",
    EMOTION: "How should viewers feel at the end — motivated, calm, hyped, emotional?",
    PACE: "Should the cuts feel slow and cinematic, or fast and punchy?",
    PLATFORM: "Where will you post it — Instagram, TikTok, YouTube Shorts, or YouTube?",
}


def vocabulary() -> set[str]:
    """Every token spell-correction may map to."""
    vocab: set[str] = set()
    for phrase in CUES:
        vocab.update(phrase.split())
    vocab.update(SECTION_WORDS, INTENSIFIERS, NEGATORS)
    return {w for w in vocab if w.isalpha()}
