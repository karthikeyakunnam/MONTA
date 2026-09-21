"""
MONTA — Model Manager
=======================
Manages loading and switching between local LLM models.
"""


class ModelManager:
    """Manages local model loading and inference routing."""

    AVAILABLE_MODELS = {
        "qwen3": "models.qwen3.loader",
        "llama3": "models.llama3.loader",
    }

    def __init__(self):
        self.loaded_models = {}

    async def load_model(self, model_name: str):
        """Load a model into memory."""
        # TODO: Dynamic model loading
        pass

    async def unload_model(self, model_name: str):
        """Unload a model from memory."""
        # TODO: Free GPU memory
        pass

    async def infer(self, model_name: str, prompt: str, **kwargs) -> str:
        """Run inference on a loaded model."""
        # TODO: Route to appropriate model
        return ""
