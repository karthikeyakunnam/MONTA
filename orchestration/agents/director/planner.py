"""
MONTA — Director Planner
==========================
Task planning logic for the Director Agent.
"""


class DirectorPlanner:
    """Decomposes high-level intent into actionable tasks."""

    async def decompose_intent(self, intent: dict, clips: list) -> list:
        """Break down editing intent into ordered subtasks."""
        # TODO: Use LLM to create task breakdown
        return []

    async def prioritize_tasks(self, tasks: list) -> list:
        """Order tasks by dependency and importance."""
        # TODO: Topological sort based on dependencies
        return tasks

    async def estimate_complexity(self, tasks: list) -> dict:
        """Estimate time/resource complexity for the plan."""
        return {"estimated_time": 0, "complexity": "medium"}
