"""MONTA — Embedding Generator."""


class EmbeddingGenerator:
    """Generate embeddings for text and video descriptions."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        # TODO: Load sentence-transformers model

    async def embed_text(self, text: str) -> list:
        """Generate embedding for text."""
        # TODO: Run model inference
        return []

    async def embed_batch(self, texts: list) -> list:
        """Generate embeddings for a batch of texts."""
        return []
