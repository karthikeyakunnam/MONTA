"""MONTA — Transition Planner."""


class TransitionPlanner:
    """Plans transitions between clips based on style and mood."""

    TRANSITION_TYPES = ["cut", "fade", "dissolve", "zoom_in", "zoom_out", "slide", "glitch", "flash", "smash_cut"]

    async def plan_transitions(self, timeline: list, style: dict) -> list:
        """Assign transition types to each cut point."""
        # TODO: Use style config and mood changes to assign transitions
        for entry in timeline:
            entry["transition"] = style.get("transitions", "cut")
        return timeline
