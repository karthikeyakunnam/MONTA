"""
MONTA — OpenAI-Compatible Provider
====================================
Any ``/chat/completions`` endpoint: Qwen / Qwen-VL on DashScope
(compatible-mode), self-hosted Qwen2.5-VL or Llama 3 behind vLLM / SGLang /
TGI, or future hosted models. Vision is enabled per instance because
text-only models reject image parts.
"""

import httpx

from shared.providers.base import Capability, GenerationConfig, ImagePart, Message, ModelResponse, TextPart
from shared.providers.errors import ProviderResponseError
from shared.providers.http import HttpModelProvider


class OpenAICompatibleProvider(HttpModelProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        vision: bool = False,
        json_mode: bool = True,
        client: httpx.AsyncClient | None = None,
    ):
        super().__init__(base_url=base_url, client=client)
        self.name = name
        self.model = model
        self._api_key = api_key
        caps = {Capability.TEXT}
        if vision:
            caps.add(Capability.VISION)
        if json_mode:
            caps.add(Capability.JSON_MODE)
        self.capabilities = frozenset(caps)

    def build_payload(self, messages: list[Message], config: GenerationConfig) -> dict:
        """Translate vendor-neutral messages into a chat.completions request body."""
        out = []
        for m in messages:
            if not m.has_images:
                out.append({"role": m.role, "content": m.text})
                continue
            content = []
            for p in m.parts:
                if isinstance(p, TextPart):
                    content.append({"type": "text", "text": p.text})
                elif isinstance(p, ImagePart):
                    content.append({"type": "image_url", "image_url": {"url": f"data:{p.mime_type};base64,{p.b64()}"}})
            out.append({"role": m.role, "content": content})
        payload = {
            "model": self.model,
            "messages": out,
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
        }
        if config.json_output and Capability.JSON_MODE in self.capabilities:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def generate(self, messages: list[Message], config: GenerationConfig | None = None) -> ModelResponse:
        config = config or GenerationConfig()
        self.check_request(messages)
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        body, latency_ms = await self._post_json(
            f"{self.base_url}/chat/completions", self.build_payload(messages, config), headers, config.timeout_s
        )
        kw = {"provider": self.name, "model": self.model}

        choices = body.get("choices") or []
        if not choices:
            raise ProviderResponseError(f"{self.model_id} returned no choices", **kw)
        choice = choices[0]
        finish = choice.get("finish_reason") or ""
        if finish == "content_filter":
            raise ProviderResponseError(f"{self.model_id} filtered the response", **kw)
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):  # some servers return content parts
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        if not content or not str(content).strip():
            raise ProviderResponseError(f"{self.model_id} returned empty content (finish={finish})", **kw)

        usage = body.get("usage") or {}
        return self._observe(ModelResponse(
            text=str(content),
            provider=self.name,
            model=self.model,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
            raw_finish_reason=finish,
        ))
