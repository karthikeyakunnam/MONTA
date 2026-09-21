"""
MONTA — Prompt Parser
=======================
Layer 3: Parses natural language prompts into structured data.
"""


class PromptParser:
    """Parses free-text editing prompts into structured intent."""

    async def parse(self, raw_prompt: str) -> dict:
        """
        Parse a natural language prompt into structured editing intent.
        
        Example:
            Input:  "make this feel like a dark cinematic transformation story,
                     slow beginning, emotional middle, aggressive ending,
                     orange-teal grade, dramatic bass music"
            
            Output: {
                "genre": "transformation",
                "pacing": "dynamic",
                "color": "orange_teal",
                "emotion": "motivational",
                "target": "instagram"
            }
        """
        # TODO: Use LLM (Qwen 3 or Llama 3) for intent extraction
        return {
            "genre": "",
            "pacing": "",
            "color": "",
            "emotion": "",
            "target": "",
        }
