"""
MONTA — Gemini Provider
=========================
Google Gemini via the Generative Language REST API (``generateContent``).
Supports text, images (inline data) and native JSON output.
"""

import httpx

from shared.providers.base import Capability, GenerationConfig, ImagePart, Message, ModelResponse, TextPart
from shared.providers.errors import ProviderResponseError
from shared.providers.http import HttpModelProvider

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_BLOCKING_FINISH_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


class GeminiProvider(HttpModelProvider):
    name = "gemini"
    capabilities = frozenset({Capability.TEXT, Capability.VISION, Capability.JSON_MODE})

    def __init__(self, *, api_key: str, model: str, base_url: str = GEMINI_BASE_URL, client: httpx.AsyncClient | None = None):
        if not api_key:
            raise ValueError("GeminiProvider requires an api_key")
        super().__init__(base_url=base_url, client=client)
        self.model = model
        self._api_key = api_key

    def build_payload(self, messages: list[Message], config: GenerationConfig) -> dict:
        """Translate vendor-neutral messages into a generateContent request body."""
        system_text = "\n\n".join(m.text for m in messages if m.role == "system")
        contents = []
        for m in messages:
            if m.role == "system":
                continue
            parts = []
            for p in m.parts:
                if isinstance(p, TextPart):
                    parts.append({"text": p.text})
                elif isinstance(p, ImagePart):
                    parts.append({"inlineData": {"mimeType": p.mime_type, "data": p.b64()}})
            contents.append({"role": "model" if m.role == "assistant" else "user", "parts": parts})

        generation_config = {"temperature": config.temperature, "maxOutputTokens": config.max_output_tokens}
        if config.json_output:
            generation_config["responseMimeType"] = "application/json"
        payload = {"contents": contents, "generationConfig": generation_config}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        return payload

    async def generate(self, messages: list[Message], config: GenerationConfig | None = None) -> ModelResponse:
        config = config or GenerationConfig()
        self.check_request(messages)
        body, latency_ms = await self._post_json(
            f"{self.base_url}/models/{self.model}:generateContent",
            self.build_payload(messages, config),
            {"x-goog-api-key": self._api_key, "content-type": "application/json"},
            config.timeout_s,
        )
        kw = {"provider": self.name, "model": self.model}

        block = (body.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise ProviderResponseError(f"{self.model_id} blocked the prompt: {block}", **kw)
        candidates = body.get("candidates") or []
        if not candidates:
            raise ProviderResponseError(f"{self.model_id} returned no candidates", **kw)
        candidate = candidates[0]
        finish = candidate.get("finishReason", "")
        if finish in _BLOCKING_FINISH_REASONS:
            raise ProviderResponseError(f"{self.model_id} stopped generation: {finish}", **kw)
        text = "".join(p.get("text", "") for p in (candidate.get("content") or {}).get("parts", []))
        if not text.strip():
            raise ProviderResponseError(f"{self.model_id} returned empty text (finish={finish})", **kw)

        usage = body.get("usageMetadata") or {}
        return self._observe(ModelResponse(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=latency_ms,
            raw_finish_reason=finish,
        ))
