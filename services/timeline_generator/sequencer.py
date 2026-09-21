"""
MONTA — Clip Sequencer
========================
Layer 9: Determines the order and timing of clips.

Produces:
    Clip 12: 0.0 → 2.1
    Clip 5:  2.1 → 4.4
    Clip 18: 4.4 → 7.9
"""


class ClipSequencer:
    """Orders clips into a coherent sequence."""

    async def sequence(self, story_acts: list, clip_profiles: dict, style: dict) -> list:
        """Create ordered clip sequence from story acts."""
        timeline = []
        current_time = 0.0

        for act in story_acts:
            for clip_id in act.get("clips", []):
                profile = clip_profiles.get(clip_id, {})
                duration = profile.get("duration", 3.0)

                # Apply style-based duration adjustments
                duration = self._adjust_duration(duration, style)

                timeline.append({
                    "clip_id": clip_id,
                    "start": round(current_time, 2),
                    "end": round(current_time + duration, 2),
                    "act": act.get("act_number"),
                    "transition": "cut",
                })
                current_time += duration

        return timeline

    def _adjust_duration(self, duration: float, style: dict) -> float:
        """Adjust clip duration based on cut speed style."""
        speed = style.get("cut_speed", "medium")
        multipliers = {"slow": 1.5, "medium": 1.0, "fast": 0.6, "aggressive": 0.4}
        return duration * multipliers.get(speed, 1.0)
