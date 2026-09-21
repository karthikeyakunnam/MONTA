"""MONTA — User Context Provider."""


class UserContextProvider:
    """Fetches user-specific context (history, preferences)."""

    async def get_user_context(self, user_id: str) -> dict:
        """Retrieve user's editing history and preferences."""
        # TODO: Query DB for user profile
        # TODO: Query Qdrant for preference vectors
        return {"style": "default", "history": [], "feedback": []}
