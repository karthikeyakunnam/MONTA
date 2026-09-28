"""
MONTA — Arbitration Rules
===========================
Candidate weight for model m on field f:

    w = reliability(m, f) × (0.5 + 0.5 × self_confidence)      (self_confidence 0.75 if absent)

Measured reliability dominates; self-reported confidence only modulates it.

Rules by field kind:

============  ===============================================================
categorical   weighted vote; margin < 0.10 → the single most reliable model decides
explained     categorical vote on ``.value``; merged confidence = mean supporter
              confidence × agreement; winner's reasoning kept + arbitration note
numeric       reliability-weighted mean
set           keep items supported by ≥ 50% of total weight
scored_roles  keep roles supported by ≥ 50% of weight; confidence = weighted mean
text          taken from the most-weighted model
============  ===============================================================

The merged dict is re-validated against the schema, so arbitration can never
emit something a single model could not.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from shared.arbitration.reliability import ReliabilityTable
from shared.contracts.arbitration import ArbitrationCandidate, ArbitrationRecord, ArbitrationSummary, FieldKind
from shared.observability import catalog as m
from shared.observability.tracing import span
from shared.providers.base import GenerationConfig, Message, ModelProvider
from shared.providers.errors import ProviderError, StructuredOutputError
from shared.providers.structured import generate_structured

logger = logging.getLogger("monta.arbitration")

T = TypeVar("T", bound=BaseModel)

TIE_MARGIN = 0.10
SUPPORT = 0.5
DEFAULT_SELF_CONFIDENCE = 0.75

INTENT_POLICY: dict[str, FieldKind] = {
    "genre": "explained", "emotion": "explained", "pace": "explained", "target_platform": "explained",
    "color_grade": "explained", "caption_style": "explained", "music_style": "explained",
    "reference_style": "explained", "target_duration_s": "explained",
    "pacing_arc": "text", "ambiguities": "text", "conflicts": "text",
}

VISION_POLICY: dict[str, FieldKind] = {
    "activities": "set", "objects": "set", "people": "set", "visual_tags": "set", "quality_issues": "set",
    "camera_type": "categorical", "shot_type": "categorical", "scene_type": "categorical", "emotion": "categorical",
    "emotion_confidence": "numeric", "energy_estimate": "numeric", "story_role_candidates": "scored_roles",
    "reasoning": "text",
}
VISION_SELF_CONFIDENCE = {"emotion": "emotion_confidence"}


def _plain(v: Any) -> Any:
    if isinstance(v, BaseModel):
        return v.model_dump(mode="json")
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return getattr(v, "value", v)


def _key(v: Any) -> str:
    return "∅" if v is None else str(_plain(v))


def arbitrate_outputs(
    outputs: Mapping[str, T],
    schema: type[T],
    policy: Mapping[str, FieldKind],
    reliability: ReliabilityTable,
    *,
    self_confidence_fields: Mapping[str, str] | None = None,
) -> tuple[T, list[ArbitrationRecord]]:
    """Merge several schema-valid outputs field by field. ``outputs`` maps model_id → output."""
    if not outputs:
        raise ValueError("nothing to arbitrate")
    models = list(outputs)
    merged: dict[str, Any] = {}
    records: list[ArbitrationRecord] = []
    sc_fields = dict(self_confidence_fields or {})

    for field in schema.model_fields:
        kind = policy.get(field, "text")
        values = {mid: getattr(outputs[mid], field) for mid in models}

        def self_conf(mid: str) -> float | None:
            v = values[mid]
            if kind == "explained" and v is not None:
                return float(v.confidence)
            if field in sc_fields:
                return float(getattr(outputs[mid], sc_fields[field]))
            return None

        weights = {}
        rels = {}
        for mid in models:
            rels[mid] = reliability.accuracy(mid, field)
            sc = self_conf(mid)
            weights[mid] = rels[mid] * (0.5 + 0.5 * (sc if sc is not None else DEFAULT_SELF_CONFIDENCE))
        total = sum(weights.values()) or 1.0
        most_reliable = max(models, key=lambda x: (rels[x], weights[x]))

        def cand(mid: str, shown: Any) -> ArbitrationCandidate:
            return ArbitrationCandidate(model_id=mid, value=_key(shown)[:2000], weight=round(weights[mid], 4),
                                        reliability=round(rels[mid], 4), self_confidence=self_conf(mid))

        if kind in ("categorical", "explained"):
            key_of = (lambda v: _key(v.value if v is not None else None)) if kind == "explained" else _key
            tally: dict[str, float] = defaultdict(float)
            for mid in models:
                tally[key_of(values[mid])] += weights[mid]
            ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)
            win_key, win_w = ranked[0]
            runner_w = ranked[1][1] if len(ranked) > 1 else 0.0
            margin = (win_w - runner_w) / total
            if len(ranked) == 1:
                rule = "unanimous"
            elif margin < TIE_MARGIN:
                win_key, rule = key_of(values[most_reliable]), "reliability_tiebreak"
                win_w = tally[win_key]
            else:
                rule = "weighted_vote"
            supporters = [mid for mid in models if key_of(values[mid]) == win_key]
            agreement = win_w / total
            chosen = values[max(supporters, key=lambda x: weights[x])]
            if kind == "explained" and chosen is not None:
                confs = [values[s].confidence for s in supporters]
                note = f" [arbitrated {rule}: {len(supporters)}/{len(models)} models, agreement {agreement:.2f}]"
                chosen = type(chosen).model_validate({
                    **chosen.model_dump(), "confidence": round(sum(confs) / len(confs) * agreement, 4),
                    "reasoning": (chosen.reasoning + note)[:600],
                })
            merged[field] = chosen
            records.append(ArbitrationRecord(
                schema_name=schema.__name__, field=field, kind=kind, candidates=tuple(cand(x, values[x] if kind != "explained" or values[x] is None else values[x].value) for x in models),
                winner=win_key[:2000], agreement=round(agreement, 4), margin=round(min(1.0, max(0.0, margin)), 4), rule=rule,
                rationale=f"{', '.join(f'{k}={w:.2f}' for k, w in ranked)} (weights = reliability × self-confidence)",
            ))
        elif kind == "numeric":
            val = sum(float(values[x]) * weights[x] for x in models) / total
            nums = [float(values[x]) for x in models]
            spread = max(nums) - min(nums)
            scale = max(1.0, max(abs(n) for n in nums))
            merged[field] = round(val, 4)
            records.append(ArbitrationRecord(
                schema_name=schema.__name__, field=field, kind=kind, candidates=tuple(cand(x, values[x]) for x in models),
                winner=f"{val:.4f}", agreement=round(max(0.0, 1 - spread / scale), 4), margin=0.0, rule="weighted_mean",
                rationale=f"weighted mean of {nums} (spread {spread:.2f})",
            ))
        elif kind == "set":
            support: dict[str, float] = defaultdict(float)
            display: dict[str, str] = {}
            for x in models:
                for item in values[x] or ():
                    k = str(item).strip().lower()
                    support[k] += weights[x]
                    display.setdefault(k, str(item))
            kept = [display[k] for k, w in sorted(support.items(), key=lambda kv: -kv[1]) if w / total >= SUPPORT]
            merged[field] = tuple(kept[:32])
            sets = [{str(i).strip().lower() for i in values[x] or ()} for x in models]
            union = set().union(*sets)
            jacc = (len(set.intersection(*sets)) / len(union)) if union else 1.0
            records.append(ArbitrationRecord(
                schema_name=schema.__name__, field=field, kind=kind, candidates=tuple(cand(x, list(values[x] or ())) for x in models),
                winner=_key(kept), agreement=round(jacc, 4), margin=0.0, rule="support_threshold",
                rationale=f"kept items backed by ≥{SUPPORT:.0%} of weight; dropped {len(union) - len(kept)} minority item(s)",
            ))
        elif kind == "scored_roles":
            support: dict[str, float] = defaultdict(float)
            conf_acc: dict[str, float] = defaultdict(float)
            for x in models:
                for rc in values[x] or ():
                    support[rc.role.value] += weights[x]
                    conf_acc[rc.role.value] += weights[x] * rc.confidence
            kept = [{"role": r, "confidence": round(conf_acc[r] / support[r] * min(1.0, support[r] / total), 4)}
                    for r in sorted(support, key=lambda r: -support[r]) if support[r] / total >= SUPPORT]
            merged[field] = kept
            records.append(ArbitrationRecord(
                schema_name=schema.__name__, field=field, kind=kind,
                candidates=tuple(cand(x, [f"{rc.role.value}:{rc.confidence:.2f}" for rc in values[x] or ()]) for x in models),
                winner=_key([k["role"] for k in kept]), agreement=round(len(kept) / max(1, len(support)), 4), margin=0.0,
                rule="support_threshold", rationale="roles kept when backed by ≥50% of weight; confidence weight-averaged",
            ))
        else:  # text
            leader = max(models, key=lambda x: weights[x])
            merged[field] = values[leader]
            if field == "reasoning":
                merged[field] = (f"[{leader}] " + str(values[leader]))[:2000]
            records.append(ArbitrationRecord(
                schema_name=schema.__name__, field=field, kind=kind, candidates=tuple(cand(x, "…") for x in models),
                winner=leader, agreement=round(weights[leader] / total, 4), margin=0.0, rule="most_reliable",
                rationale=f"free-form field taken from the most trusted model ({leader})",
            ))

    return schema.model_validate(merged), records


async def run_arbitrated(
    providers: Sequence[ModelProvider],
    messages: list[Message],
    schema: type[T],
    policy: Mapping[str, FieldKind],
    reliability: ReliabilityTable,
    *,
    config: GenerationConfig | None = None,
    semantic_validator: Callable[[T], list[str]] | None = None,
    self_confidence_fields: Mapping[str, str] | None = None,
    max_repairs: int = 1,
) -> tuple[T, ArbitrationSummary]:
    """Query all providers concurrently and arbitrate their schema-valid answers."""
    with span("arbitration.run", schema=schema.__name__, models=len(providers)) as sp:
        results = await asyncio.gather(*(
            generate_structured(p, messages, schema, config=config, semantic_validator=semantic_validator, max_repairs=max_repairs)
            for p in providers
        ), return_exceptions=True)
        outputs: dict[str, T] = {}
        failures: list[str] = []
        for p, r in zip(providers, results):
            if isinstance(r, BaseException):
                if not isinstance(r, (ProviderError, asyncio.TimeoutError)):
                    raise r
                failures.append(f"{p.model_id}: {type(r).__name__}: {r}"[:500])
            else:
                outputs[p.model_id] = r.value
        queried = tuple(p.model_id for p in providers)
        if not outputs:
            m.ARBITRATIONS.inc(schema=schema.__name__, outcome="failed")
            raise StructuredOutputError(f"all {len(providers)} models failed for {schema.__name__}", errors=failures)
        if len(outputs) == 1:
            (mid, value), = outputs.items()
            m.ARBITRATIONS.inc(schema=schema.__name__, outcome="single")
            sp.set(outcome="single")
            return value, ArbitrationSummary(schema_name=schema.__name__, models_queried=queried, models_succeeded=(mid,),
                                             failures=tuple(failures), consensus=1.0)
        value, records = arbitrate_outputs(outputs, schema, policy, reliability, self_confidence_fields=self_confidence_fields)
        decided = [r for r in records if r.kind in ("categorical", "explained")]
        consensus = sum(r.agreement for r in decided) / len(decided) if decided else 1.0
        outcome = "unanimous" if all(r.rule == "unanimous" for r in decided) else "resolved"
        m.ARBITRATIONS.inc(schema=schema.__name__, outcome=outcome)
        sp.set(outcome=outcome, consensus=round(consensus, 3))
        return value, ArbitrationSummary(schema_name=schema.__name__, models_queried=queried,
                                         models_succeeded=tuple(outputs), failures=tuple(failures),
                                         consensus=round(consensus, 4), records=tuple(records))
