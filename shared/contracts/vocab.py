"""
MONTA — Controlled Vocabulary
===============================
The single source of truth for every enumerated value that crosses a layer
boundary. LLM outputs, lexicon rules, story patterns and validators all bind to
these enums, so an unknown label is rejected at the schema boundary instead of
silently propagating.

Adding a value is backwards compatible. Renaming or removing one is a breaking
change and requires a contract version bump.
"""

from enum import StrEnum


class Genre(StrEnum):
    SPORTS = "sports"
    FITNESS = "fitness"
    TRAVEL = "travel"
    LUXURY = "luxury"
    FASHION = "fashion"
    LIFESTYLE = "lifestyle"
    BUSINESS = "business"
    EDUCATION = "education"
    CINEMATIC = "cinematic"
    DOCUMENTARY = "documentary"
    EVENT = "event"
    WEDDING = "wedding"
    PRODUCT = "product"
    PODCAST = "podcast"


class Emotion(StrEnum):
    MOTIVATIONAL = "motivational"
    INSPIRING = "inspiring"
    EMOTIONAL = "emotional"
    INTENSE = "intense"
    ENERGETIC = "energetic"
    DRAMATIC = "dramatic"
    LUXURY = "luxury"
    UPLIFTING = "uplifting"
    AGGRESSIVE = "aggressive"
    CALM = "calm"
    ROMANTIC = "romantic"
    JOYFUL = "joyful"
    NOSTALGIC = "nostalgic"
    NEUTRAL = "neutral"


class Pace(StrEnum):
    SLOW = "slow"
    MEDIUM = "medium"
    FAST = "fast"
    AGGRESSIVE = "aggressive"


# Emotions close enough that one satisfies a request for the other.
EMOTION_AFFINITY: tuple[frozenset[Emotion], ...] = (
    frozenset({Emotion.MOTIVATIONAL, Emotion.INSPIRING, Emotion.UPLIFTING}),
    frozenset({Emotion.MOTIVATIONAL, Emotion.INTENSE}),
    frozenset({Emotion.INTENSE, Emotion.AGGRESSIVE, Emotion.DRAMATIC}),
    frozenset({Emotion.ENERGETIC, Emotion.JOYFUL}),
    frozenset({Emotion.ENERGETIC, Emotion.AGGRESSIVE}),
    frozenset({Emotion.CALM, Emotion.ROMANTIC}),
    frozenset({Emotion.EMOTIONAL, Emotion.NOSTALGIC, Emotion.ROMANTIC}),
    frozenset({Emotion.LUXURY, Emotion.CALM}),
)

PACE_ORDER: dict[Pace, int] = {Pace.SLOW: 0, Pace.MEDIUM: 1, Pace.FAST: 2, Pace.AGGRESSIVE: 3}


class Platform(StrEnum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    YOUTUBE_SHORT = "youtube_short"
    YOUTUBE = "youtube"
    GENERAL = "general"


class ColorGrade(StrEnum):
    DARK = "dark"
    ORANGE_TEAL = "orange_teal"
    BW = "bw"
    VIBRANT = "vibrant"
    NATURAL = "natural"
    VINTAGE = "vintage"
    NEON = "neon"
    WARM = "warm"
    COOL = "cool"
    HIGH_CONTRAST = "high_contrast"


class CaptionStyle(StrEnum):
    NIKE = "Nike"
    GYMSHARK = "Gymshark"
    DAVID_GOGGINS = "David Goggins"
    CINEMATIC_DOCUMENTARY = "Cinematic Documentary"
    MOTIVATIONAL = "Motivational"
    ALPHA_MINDSET = "Alpha Mindset"
    MINIMAL = "Minimal"
    NONE = "None"


class MusicStyle(StrEnum):
    EPIC_ORCHESTRAL = "epic orchestral"
    HARD_TRAP = "hard trap"
    CINEMATIC_TRAILER = "cinematic trailer"
    EMOTIONAL_PIANO = "emotional piano"
    HYBRID_CINEMATIC = "hybrid cinematic"
    SPORTS_ANTHEM = "sports anthem"
    LOFI = "lo-fi"
    ACOUSTIC = "acoustic"
    ELECTRONIC = "electronic"
    AMBIENT = "ambient"


class StoryRole(StrEnum):
    """What narrative job a clip can perform. Assigned by Layer 6, consumed by Layer 7."""

    ESTABLISHING = "establishing"
    STRUGGLE = "struggle"
    PREPARATION = "preparation"
    PROGRESS = "progress"
    INTENSITY = "intensity"
    PEAK = "peak"
    HERO = "hero"
    PAYOFF = "payoff"
    DETAIL = "detail"
    REACTION = "reaction"
    TALKING_HEAD = "talking_head"
    PRODUCT_REVEAL = "product_reveal"
    CELEBRATION = "celebration"
    TRANSITION = "transition"
    B_ROLL = "b_roll"


class ShotType(StrEnum):
    EXTREME_WIDE = "extreme_wide"
    WIDE = "wide"
    MEDIUM = "medium"
    CLOSE_UP = "close_up"
    EXTREME_CLOSE_UP = "extreme_close_up"
    POV = "pov"
    OVER_SHOULDER = "over_shoulder"
    INSERT = "insert"
    UNKNOWN = "unknown"


class CameraType(StrEnum):
    PHONE = "phone"
    ACTION_CAM = "action_cam"
    DRONE = "drone"
    CINEMA = "cinema"
    WEBCAM = "webcam"
    SCREEN_RECORDING = "screen_recording"
    UNKNOWN = "unknown"


class CameraMotion(StrEnum):
    STATIC = "static"
    PAN = "pan"
    TILT = "tilt"
    TRACKING = "tracking"
    HANDHELD = "handheld"
    SHAKY = "shaky"
    DYNAMIC = "dynamic"
    UNKNOWN = "unknown"


class Lighting(StrEnum):
    LOW = "low"
    GOOD = "good"
    OVEREXPOSED = "overexposed"
    FLAT = "flat"
    UNKNOWN = "unknown"
