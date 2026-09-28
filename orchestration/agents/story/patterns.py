"""
MONTA — Story Pattern Library (Layer 7)
=========================================
MONTA's moat: stories are not invented from scratch per request. They are
selected from a versioned library of proven narrative structures, then
adapted to the intent, the footage and the creator's history.

Every pattern is validated at import time (shares sum to 1, one pacing value
per act, energy ranges in bounds), so a malformed pattern can never ship.

To evolve a pattern, bump its ``version``; narrative memory records the
pattern id, and priors are shared across versions deliberately — structure
changes should be small enough that history still applies. Create a new
``pattern_id`` for anything structurally different.
"""

from shared.contracts.story import ActTemplate, PatternValidation, StoryPattern
from shared.contracts.vocab import Emotion as E
from shared.contracts.vocab import Genre as G
from shared.contracts.vocab import StoryRole as R


# Validation profiles. Talking-head formats are low-energy by nature and progress through content,
# not motion; reflective formats (wedding, travel, montage) end calm on purpose.
TALKING_HEAD = PatternValidation(low_energy_threshold=2.0, max_low_energy_run=8, min_act_energy_delta=0.0,
                                 hold_tolerance=4.0, spike_scale=1.0)
REFLECTIVE = PatternValidation(low_energy_threshold=3.0, max_low_energy_run=4, min_act_energy_delta=0.3)


def _act(act_id, name, purpose, energy, direction, roles, share, emotions=()):
    return ActTemplate(act_id=act_id, name=name, purpose=purpose, target_energy=energy, energy_direction=direction,
                       preferred_roles=tuple(roles), preferred_emotions=tuple(emotions), share=share)


PATTERNS: tuple[StoryPattern, ...] = (
    StoryPattern(
        pattern_id="transformation", name="Transformation", version=1,
        description="Before → grind → breakthrough → reveal. The audience earns the payoff by seeing the struggle.",
        genres=(G.FITNESS, G.SPORTS, G.LIFESTYLE, G.BUSINESS),
        emotions=(E.MOTIVATIONAL, E.INSPIRING, E.EMOTIONAL, E.INTENSE),
        keywords=("transformation", "journey", "before and after", "glow up", "progress", "comeback"),
        acts=(
            _act("struggle", "Struggle", "Establish the starting point so the change means something", (2, 5), "hold",
                 [R.STRUGGLE, R.ESTABLISHING, R.PREPARATION], 0.2, [E.EMOTIONAL, E.DRAMATIC]),
            _act("grind", "Grind", "Show the work; build momentum", (4, 7), "rise",
                 [R.PREPARATION, R.PROGRESS, R.DETAIL], 0.3, [E.MOTIVATIONAL, E.INTENSE]),
            _act("breakthrough", "Breakthrough", "Peak effort — the most impressive moments", (6, 9.5), "rise",
                 [R.INTENSITY, R.PEAK], 0.3, [E.INTENSE, E.AGGRESSIVE, E.ENERGETIC]),
            _act("reveal", "Reveal", "Payoff: the result and the hero shot", (6, 10), "hold",
                 [R.HERO, R.PAYOFF, R.CELEBRATION], 0.2, [E.INSPIRING, E.MOTIVATIONAL, E.UPLIFTING]),
        ),
        required_emotions=(E.MOTIVATIONAL, E.INSPIRING), pacing_curve=(1.3, 1.0, 0.75, 1.2), hero_moment_position=0.9,
    ),
    StoryPattern(
        pattern_id="fitness_reel", name="Fitness Reel", version=1,
        description="Warm-up → working sets → max effort → finisher. Relentless escalation for training content.",
        genres=(G.FITNESS, G.SPORTS),
        emotions=(E.ENERGETIC, E.AGGRESSIVE, E.INTENSE, E.MOTIVATIONAL),
        keywords=("gym", "workout", "pr", "leg day", "training", "lift"),
        acts=(
            _act("warmup", "Warm-up", "Set the scene and the athlete", (3, 6), "rise",
                 [R.PREPARATION, R.ESTABLISHING, R.DETAIL], 0.2, [E.INTENSE]),
            _act("work", "Working sets", "Volume and rhythm — sell the effort", (5, 8), "rise",
                 [R.PROGRESS, R.INTENSITY], 0.35, [E.ENERGETIC, E.INTENSE]),
            _act("max_effort", "Max effort", "The heaviest, fastest, hardest reps", (7, 10), "rise",
                 [R.INTENSITY, R.PEAK], 0.3, [E.AGGRESSIVE, E.INTENSE]),
            _act("finisher", "Finisher", "Victory beat and hero shot", (6, 9.5), "hold",
                 [R.HERO, R.PAYOFF, R.CELEBRATION], 0.15, [E.MOTIVATIONAL, E.INSPIRING]),
        ),
        required_emotions=(E.ENERGETIC, E.INTENSE), pacing_curve=(1.0, 0.8, 0.6, 1.1), hero_moment_position=0.88,
    ),
    StoryPattern(
        pattern_id="motivational_reel", name="Motivational Reel", version=1,
        description="Doubt → decision → relentless → triumph. Emotion-first structure for mindset content.",
        genres=(G.FITNESS, G.SPORTS, G.BUSINESS, G.LIFESTYLE),
        emotions=(E.MOTIVATIONAL, E.INSPIRING, E.DRAMATIC, E.INTENSE),
        keywords=("motivation", "never give up", "discipline", "mindset", "grind", "no excuses"),
        acts=(
            _act("doubt", "Doubt", "Quiet, heavy opening that makes the viewer lean in", (2, 5), "hold",
                 [R.STRUGGLE, R.ESTABLISHING, R.REACTION], 0.2, [E.DRAMATIC, E.EMOTIONAL]),
            _act("decision", "Decision", "The turn — preparation and focus", (3, 6.5), "rise",
                 [R.PREPARATION, R.DETAIL, R.TRANSITION], 0.2, [E.INTENSE, E.MOTIVATIONAL]),
            _act("relentless", "Relentless", "Escalating proof of effort", (6, 9.5), "rise",
                 [R.INTENSITY, R.PROGRESS, R.PEAK], 0.4, [E.MOTIVATIONAL, E.AGGRESSIVE, E.INTENSE]),
            _act("triumph", "Triumph", "The payoff the viewer was promised", (7, 10), "hold",
                 [R.HERO, R.PAYOFF, R.CELEBRATION], 0.2, [E.INSPIRING, E.UPLIFTING]),
        ),
        required_emotions=(E.MOTIVATIONAL,), pacing_curve=(1.4, 1.0, 0.7, 1.2), hero_moment_position=0.88,
    ),
    StoryPattern(
        pattern_id="travel", name="Travel", version=1,
        description="Arrival → exploration → highlight → reflection. Sense of place first, wonder at the peak.",
        genres=(G.TRAVEL, G.LIFESTYLE),
        emotions=(E.UPLIFTING, E.CALM, E.JOYFUL, E.INSPIRING, E.NOSTALGIC),
        keywords=("travel", "trip", "vacation", "explore", "adventure", "road trip"),
        acts=(
            _act("arrival", "Arrival", "Establish where we are", (3, 5.5), "rise",
                 [R.ESTABLISHING, R.TRANSITION], 0.2, [E.CALM, E.UPLIFTING]),
            _act("explore", "Exploration", "Texture of the place — people, food, details", (4, 7), "hold",
                 [R.B_ROLL, R.DETAIL, R.PROGRESS], 0.35, [E.JOYFUL, E.UPLIFTING]),
            _act("highlight", "Highlight", "The unforgettable moment of the trip", (6, 9), "rise",
                 [R.PEAK, R.HERO, R.CELEBRATION], 0.25, [E.JOYFUL, E.INSPIRING]),
            _act("reflection", "Reflection", "Wind down; leave a feeling", (2, 5.5), "fall",
                 [R.PAYOFF, R.ESTABLISHING, R.B_ROLL], 0.2, [E.NOSTALGIC, E.CALM]),
        ),
        required_emotions=(E.UPLIFTING, E.JOYFUL), pacing_curve=(1.2, 1.0, 0.8, 1.4), hero_moment_position=0.7,
        validation=REFLECTIVE,
    ),
    StoryPattern(
        pattern_id="event", name="Event Recap", version=1,
        description="Anticipation → arrival → main moment → afterglow.",
        genres=(G.EVENT, G.LIFESTYLE, G.BUSINESS),
        emotions=(E.ENERGETIC, E.JOYFUL, E.UPLIFTING),
        keywords=("event", "concert", "festival", "party", "recap", "aftermovie", "conference"),
        acts=(
            _act("anticipation", "Anticipation", "Build-up: setup, crowds gathering", (3, 5.5), "rise",
                 [R.ESTABLISHING, R.PREPARATION], 0.2, [E.ENERGETIC]),
            _act("arrival", "Arrival", "The room fills; energy rises", (4, 7.5), "rise",
                 [R.B_ROLL, R.REACTION, R.PROGRESS], 0.3, [E.JOYFUL, E.ENERGETIC]),
            _act("main_moment", "Main moment", "The drop, the speech, the headline act", (7, 10), "rise",
                 [R.PEAK, R.HERO, R.CELEBRATION], 0.3, [E.ENERGETIC, E.JOYFUL]),
            _act("afterglow", "Afterglow", "Smiles and exits — the memory", (3, 6.5), "fall",
                 [R.REACTION, R.PAYOFF, R.B_ROLL], 0.2, [E.UPLIFTING, E.NOSTALGIC]),
        ),
        required_emotions=(E.ENERGETIC, E.JOYFUL), pacing_curve=(1.1, 0.9, 0.7, 1.3), hero_moment_position=0.7,
    ),
    StoryPattern(
        pattern_id="product_launch", name="Product Launch", version=1,
        description="Tease → problem → reveal → proof → call to action.",
        genres=(G.PRODUCT, G.BUSINESS, G.LUXURY, G.FASHION),
        emotions=(E.INSPIRING, E.LUXURY, E.ENERGETIC, E.DRAMATIC),
        keywords=("launch", "product", "unboxing", "drop", "reveal", "promo", "ad", "commercial"),
        acts=(
            _act("tease", "Tease", "Intrigue with partial glimpses", (2, 5.5), "rise",
                 [R.DETAIL, R.ESTABLISHING], 0.15, [E.DRAMATIC, E.LUXURY]),
            _act("problem", "Problem", "Why the viewer should care", (3, 5.5), "hold",
                 [R.STRUGGLE, R.TALKING_HEAD, R.B_ROLL], 0.2, [E.DRAMATIC]),
            _act("reveal", "Reveal", "The product, beautifully", (6, 9), "rise",
                 [R.PRODUCT_REVEAL, R.HERO], 0.25, [E.INSPIRING, E.LUXURY]),
            _act("proof", "Proof", "It works — use, details, reactions", (5, 8), "hold",
                 [R.DETAIL, R.PROGRESS, R.REACTION], 0.25, [E.ENERGETIC, E.UPLIFTING]),
            _act("cta", "Call to action", "Close on the product and brand", (5, 8), "hold",
                 [R.PAYOFF, R.PRODUCT_REVEAL], 0.15, [E.INSPIRING]),
        ),
        required_emotions=(E.INSPIRING,), pacing_curve=(1.0, 1.1, 0.8, 0.9, 1.2), hero_moment_position=0.5,
        hero_roles=(R.PRODUCT_REVEAL, R.HERO, R.PEAK),
    ),
    StoryPattern(
        pattern_id="wedding", name="Wedding", version=1,
        description="Preparation → ceremony → celebration → send-off. The vows are the hero moment.",
        genres=(G.WEDDING, G.EVENT),
        emotions=(E.ROMANTIC, E.EMOTIONAL, E.JOYFUL),
        keywords=("wedding", "bride", "groom", "vows", "ceremony", "reception"),
        acts=(
            _act("preparation", "Preparation", "Anticipation and detail", (2, 4.5), "rise",
                 [R.PREPARATION, R.DETAIL, R.ESTABLISHING], 0.2, [E.ROMANTIC, E.CALM]),
            _act("ceremony", "Ceremony", "The emotional core — vows, reactions", (3, 6.5), "rise",
                 [R.REACTION, R.HERO, R.PEAK], 0.3, [E.EMOTIONAL, E.ROMANTIC]),
            _act("celebration", "Celebration", "Release — dancing, toasts", (6, 9), "rise",
                 [R.CELEBRATION, R.B_ROLL], 0.3, [E.JOYFUL, E.ENERGETIC]),
            _act("send_off", "Send-off", "The last look back", (3, 6), "fall",
                 [R.PAYOFF, R.REACTION], 0.2, [E.ROMANTIC, E.NOSTALGIC]),
        ),
        required_emotions=(E.ROMANTIC, E.EMOTIONAL), pacing_curve=(1.4, 1.3, 0.9, 1.4), hero_moment_position=0.4,
        hero_roles=(R.HERO, R.REACTION, R.PEAK),
        validation=REFLECTIVE,
    ),
    StoryPattern(
        pattern_id="podcast_highlight", name="Podcast Highlight", version=1,
        description="Hook → context → insight → punchline. Lead with the strongest line to stop the scroll.",
        genres=(G.PODCAST, G.BUSINESS, G.EDUCATION),
        emotions=(E.INSPIRING, E.DRAMATIC, E.EMOTIONAL, E.JOYFUL),
        keywords=("podcast", "episode", "interview", "clip", "conversation"),
        acts=(
            _act("hook", "Hook", "Open on the most arresting moment", (4.5, 8), "hold",
                 [R.HERO, R.REACTION, R.PEAK, R.TALKING_HEAD], 0.2, [E.DRAMATIC]),
            _act("context", "Context", "Who is speaking and why it matters", (3, 6), "hold",
                 [R.TALKING_HEAD, R.B_ROLL, R.ESTABLISHING], 0.3, [E.NEUTRAL, E.INSPIRING]),
            _act("insight", "Insight", "The core idea, building", (4, 7.5), "rise",
                 [R.TALKING_HEAD, R.PEAK, R.PROGRESS], 0.3, [E.INSPIRING, E.EMOTIONAL]),
            _act("punchline", "Punchline", "Land it and leave", (5, 8.5), "hold",
                 [R.REACTION, R.PAYOFF, R.TALKING_HEAD], 0.2, [E.JOYFUL, E.INSPIRING]),
        ),
        required_emotions=(E.INSPIRING,), pacing_curve=(0.9, 1.2, 1.1, 1.0), hero_moment_position=0.08,
        hero_roles=(R.HERO, R.PEAK, R.REACTION),
        validation=TALKING_HEAD,
    ),
    StoryPattern(
        pattern_id="tutorial", name="Tutorial", version=1,
        description="Result preview → setup → steps → result. Show the destination first, then the route.",
        genres=(G.EDUCATION, G.BUSINESS, G.PRODUCT, G.LIFESTYLE),
        emotions=(E.INSPIRING, E.CALM, E.UPLIFTING),
        keywords=("tutorial", "how to", "step by step", "guide", "learn", "tips", "explain"),
        acts=(
            _act("preview", "Result preview", "Promise the outcome in the first seconds", (4, 7), "hold",
                 [R.PAYOFF, R.PRODUCT_REVEAL, R.DETAIL], 0.15, [E.INSPIRING]),
            _act("setup", "Setup", "Tools, context, starting point", (2, 5), "hold",
                 [R.ESTABLISHING, R.TALKING_HEAD, R.DETAIL], 0.25, [E.CALM]),
            _act("steps", "Steps", "The process, in order", (3, 6.5), "rise",
                 [R.PROGRESS, R.DETAIL, R.TALKING_HEAD], 0.4, [E.CALM, E.UPLIFTING]),
            _act("result", "Result", "The finished outcome — the hero", (5, 8.5), "rise",
                 [R.HERO, R.PAYOFF], 0.2, [E.UPLIFTING, E.INSPIRING]),
        ),
        required_emotions=(E.UPLIFTING,), pacing_curve=(0.9, 1.2, 1.1, 1.1), hero_moment_position=0.92,
        validation=TALKING_HEAD,
    ),
    StoryPattern(
        pattern_id="cinematic_montage", name="Cinematic Montage", version=1,
        description="Establish → develop → crescendo → resolve. Image-led, music-driven.",
        genres=(G.CINEMATIC, G.DOCUMENTARY, G.TRAVEL, G.FASHION, G.LUXURY, G.SPORTS),
        emotions=(E.DRAMATIC, E.EMOTIONAL, E.INSPIRING, E.LUXURY, E.CALM),
        keywords=("cinematic", "montage", "film", "movie", "trailer", "short film"),
        acts=(
            _act("establish", "Establish", "World and tone", (2, 5), "rise",
                 [R.ESTABLISHING, R.B_ROLL], 0.2, [E.CALM, E.DRAMATIC]),
            _act("develop", "Develop", "Layer imagery; raise stakes", (4, 7), "rise",
                 [R.B_ROLL, R.DETAIL, R.PROGRESS], 0.3, [E.DRAMATIC, E.EMOTIONAL]),
            _act("crescendo", "Crescendo", "The strongest images at the music's peak", (7, 10), "rise",
                 [R.PEAK, R.INTENSITY, R.HERO], 0.3, [E.DRAMATIC, E.INSPIRING]),
            _act("resolve", "Resolve", "Let it breathe; final image", (3, 6), "fall",
                 [R.PAYOFF, R.ESTABLISHING], 0.2, [E.EMOTIONAL, E.CALM]),
        ),
        required_emotions=(E.DRAMATIC,), pacing_curve=(1.5, 1.1, 0.7, 1.5), hero_moment_position=0.75,
        validation=REFLECTIVE,
    ),
)


class PatternLibrary:
    """Lookup over validated patterns. Immutable after construction."""

    def __init__(self, patterns: tuple[StoryPattern, ...] = PATTERNS):
        ids = [p.pattern_id for p in patterns]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate pattern ids in library")
        self._by_id = {p.pattern_id: p for p in patterns}

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, pattern_id: str) -> StoryPattern:
        return self._by_id[pattern_id]

    @property
    def ids(self) -> list[str]:
        return list(self._by_id)


DEFAULT_LIBRARY = PatternLibrary()
