"""
MONTA — Structured Generation
===============================
Turn free-text model output into a validated pydantic object:

1. Extract the first JSON object (tolerates code fences and chatter).
2. Validate against the schema (enums, ranges, required fields).
3. Run an optional semantic validator (e.g. "every clip_id must exist").
4. On failure, feed the exact errors back and ask for a correction.

No unvalidated model output ever crosses a layer boundary.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from shared.providers.base import GenerationConfig, Message, ModelProvider, ModelResponse
from shared.observability import catalog as m
from shared.providers.errors import StructuredOutputError

M = TypeVar("M", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def extract_json_object(text: str) -> dict | None:
    """Return the first top-level JSON object in ``text``, or None."""
    stripped = _FENCE.sub("", (text or "").strip())
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            data, _ = decoder.raw_decode(stripped, i)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def format_validation_errors(error: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in error.errors()]


@dataclass
class StructuredAttempt:
    attempt: int
    errors: list[str]
    latency_ms: float


@dataclass
class StructuredResult(Generic[M]):
    value: M
    response: ModelResponse
    attempts: list[StructuredAttempt] = field(default_factory=list)


async def generate_structured(
    provider: ModelProvider,
    messages: list[Message],
    schema: type[M],
    *,
    config: GenerationConfig | None = None,
    semantic_validator: Callable[[M], list[str]] | None = None,
    max_repairs: int = 1,
) -> StructuredResult[M]:
    """Generate, validate and (if needed) repair a structured response."""
    config = config or GenerationConfig(json_output=True)
    conversation = list(messages)
    attempts: list[StructuredAttempt] = []

    for attempt in range(1, max_repairs + 2):
        response = await provider.generate(conversation, config)
        errors: list[str]
        value: M | None = None
        data = extract_json_object(response.text)
        if data is None:
            errors = ["response did not contain a JSON object"]
        else:
            try:
                value = schema.model_validate(data)
                errors = semantic_validator(value) if semantic_validator else []
            except ValidationError as e:
                errors = format_validation_errors(e)
        attempts.append(StructuredAttempt(attempt, errors, response.latency_ms))
        if not errors and value is not None:
            m.STRUCTURED_ATTEMPTS.observe(attempt, schema=schema.__name__, model=provider.model_id)
            return StructuredResult(value=value, response=response, attempts=attempts)
        conversation += [
            Message.assistant(response.text),
            Message.user(
                "Your previous response was rejected for these reasons:\n"
                + "\n".join(f"- {e}" for e in errors[:20])
                + "\nReturn only the corrected JSON object."
            ),
        ]

    m.STRUCTURED_FAILURES.inc(schema=schema.__name__, model=provider.model_id)
    raise StructuredOutputError(
        f"{provider.model_id} failed to produce valid {schema.__name__} after {len(attempts)} attempts",
        errors=attempts[-1].errors,
        provider=provider.name,
        model=provider.model,
    )


def schema_prompt(schema: type[BaseModel]) -> str:
    """Compact JSON schema for inclusion in prompts."""
    return json.dumps(schema.model_json_schema(), separators=(",", ":"))
