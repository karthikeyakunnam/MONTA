"""
MONTA — Video Intelligence Evaluation Metrics
===============================================
Runs the real ``VideoIntelligenceTeam`` over manifest clips and scores the
fused ``ClipIntelligence`` against labels.
"""

import asyncio
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from evaluation.video_intelligence.manifest import ManifestEntry
from orchestration.agents.intelligence import MediaToolError, VideoIntelligenceTeam
from shared.contracts.clip import ClipIntelligence, ClipSource
from shared.text import phrase_in, tokenize

CATEGORICAL = ("emotion", "scene_type", "shot_type", "camera_motion", "lighting")


class CategoricalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int
    accuracy: float | None
    labels: tuple[str, ...]
    confusion: dict[str, dict[str, int]]  # truth → predicted → count


class ScalarResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int
    spearman: float | None
    mean_abs_error: float | None


class FailureCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    clip_id: str
    kind: str
    detail: str


class VideoEvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    clips: int
    analyzed: int
    vision_model: str | None
    activity: dict[str, float | int | None]
    categorical: dict[str, CategoricalResult]
    roles: dict[str, float | int | None]
    quality: ScalarResult
    energy: ScalarResult
    quality_consistency: ScalarResult | None
    failures: tuple[FailureCase, ...]
    worst: tuple[FailureCase, ...]


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    return round(statistics.correlation(_ranks(a), _ranks(b)), 4)


def _norm(v) -> str:
    return str(getattr(v, "value", v) if v is not None else "none").strip().lower()


def _categorical(pairs: list[tuple[str, str]]) -> CategoricalResult:
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for truth, pred in pairs:
        confusion[truth][pred] += 1
    labels = tuple(sorted({t for t, _ in pairs} | {p for _, p in pairs}))
    acc = round(sum(t == p for t, p in pairs) / len(pairs), 4) if pairs else None
    return CategoricalResult(n=len(pairs), accuracy=acc, labels=labels,
                             confusion={t: dict(v) for t, v in confusion.items()})


def _activity_match(pred: tuple[str, ...], truth: tuple[str, ...]) -> tuple[int, int]:
    """(truth labels recalled, predicted labels that match some truth label) on token boundaries."""
    pred_tokens = [tokenize(p) for p in pred]
    truth_tokens = [tokenize(t) for t in truth]
    recalled = sum(1 for t in truth if any(phrase_in(pt, t) or phrase_in(tokenize(t), " ".join(pt)) for pt in pred_tokens))
    precise = sum(1 for p in pred if any(phrase_in(tt, p) or phrase_in(tokenize(p), " ".join(tt)) for tt in truth_tokens))
    return recalled, precise


async def _reencode(ffmpeg: str, src: str, out: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-v", "error", "-y", "-i", src, "-vf", "scale=iw/2:-2", "-c:v", "libx264", "-crf", "32", "-an", str(out),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise MediaToolError(f"re-encode failed: {err.decode(errors='replace')[-200:]}")


async def evaluate(entries: list[ManifestEntry], team: VideoIntelligenceTeam, *, consistency: bool = False,
                   ffmpeg: str = "ffmpeg", worst_n: int = 10) -> VideoEvalReport:
    results: dict[str, ClipIntelligence] = {}
    failures: list[FailureCase] = []
    for e in entries:
        try:
            meta = await team.media.probe(ClipSource(clip_id=e.clip_id, path=e.path))
            results[e.clip_id] = await team.analyze(meta)
        except Exception as ex:  # every failure is a finding, not a crash of the evaluation
            failures.append(FailureCase(clip_id=e.clip_id, kind="analysis_error", detail=f"{type(ex).__name__}: {ex}"[:300]))
    for cid, ci in results.items():
        if ci.provenance.degraded:
            failures.append(FailureCase(clip_id=cid, kind="degraded", detail="; ".join(ci.provenance.degraded)[:300]))
        if not ci.usable:
            failures.append(FailureCase(clip_id=cid, kind="unusable", detail=ci.unusable_reason or ""))

    labeled = [(e, results[e.clip_id]) for e in entries if e.clip_id in results]
    worst: list[tuple[float, FailureCase]] = []

    recalled = truth_n = precise = pred_n = hits = act_n = 0
    for e, ci in labeled:
        if e.labels.activities:
            r, p = _activity_match(ci.activities, e.labels.activities)
            recalled += r
            truth_n += len(e.labels.activities)
            precise += p
            pred_n += len(ci.activities)
            hits += r > 0
            act_n += 1
            if r == 0:
                worst.append((1.0, FailureCase(clip_id=e.clip_id, kind="activity_miss",
                                               detail=f"predicted {list(ci.activities)} vs truth {list(e.labels.activities)}")))
    activity = {"n": act_n, "hit_rate": round(hits / act_n, 4) if act_n else None,
                "recall": round(recalled / truth_n, 4) if truth_n else None,
                "precision": round(precise / pred_n, 4) if pred_n else None}

    categorical = {}
    for field in CATEGORICAL:
        pairs = []
        for e, ci in labeled:
            truth = getattr(e.labels, field)
            if truth is None:
                continue
            pred = getattr(ci, field)
            pairs.append((_norm(truth), _norm(pred)))
            if _norm(truth) != _norm(pred):
                worst.append((0.8, FailureCase(clip_id=e.clip_id, kind=f"{field}_miss", detail=f"predicted {_norm(pred)} vs truth {_norm(truth)}")))
        categorical[field] = _categorical(pairs)

    top1 = top3 = role_n = 0
    for e, ci in labeled:
        if not e.labels.story_roles:
            continue
        role_n += 1
        ranked = [r.role for r in sorted(ci.story_role_candidates, key=lambda r: -r.confidence)]
        truth = set(e.labels.story_roles)
        top1 += bool(ranked[:1]) and ranked[0] in truth
        top3 += bool(set(ranked[:3]) & truth)
    roles = {"n": role_n, "top1": round(top1 / role_n, 4) if role_n else None, "top3": round(top3 / role_n, 4) if role_n else None}

    def scalar(field: str, attr: str) -> ScalarResult:
        pairs = [(getattr(e.labels, field), getattr(ci, attr)) for e, ci in labeled if getattr(e.labels, field) is not None]
        if not pairs:
            return ScalarResult(n=0, spearman=None, mean_abs_error=None)
        for (truth, pred), (e, _) in zip(pairs, [x for x in labeled if getattr(x[0].labels, field) is not None]):
            if abs(truth - pred) >= 3:
                worst.append((abs(truth - pred) / 10, FailureCase(clip_id=e.clip_id, kind=f"{field}_error",
                                                                  detail=f"predicted {pred:.1f} vs truth {truth:.1f}")))
        return ScalarResult(n=len(pairs), spearman=spearman([t for t, _ in pairs], [p for _, p in pairs]),
                            mean_abs_error=round(statistics.fmean(abs(t - p) for t, p in pairs), 4))

    consistency_result = None
    if consistency and results:
        diffs = []
        with tempfile.TemporaryDirectory() as tmp:
            for e in entries:
                if e.clip_id not in results:
                    continue
                out = Path(tmp) / f"{e.clip_id}.mp4"
                try:
                    await _reencode(ffmpeg, e.path, out)
                    meta = await team.media.probe(ClipSource(clip_id=f"{e.clip_id}__reenc", path=str(out)))
                    again = await team.analyze(meta)
                    diffs.append((results[e.clip_id].quality_score, again.quality_score))
                except Exception as ex:
                    failures.append(FailureCase(clip_id=e.clip_id, kind="consistency_error", detail=str(ex)[:300]))
        if diffs:
            consistency_result = ScalarResult(n=len(diffs), spearman=spearman([a for a, _ in diffs], [b for _, b in diffs]),
                                              mean_abs_error=round(statistics.fmean(abs(a - b) for a, b in diffs), 4))

    vision = next((ci.provenance.vision_model for ci in results.values() if ci.provenance.vision_model), None)
    worst.sort(key=lambda x: -x[0])
    return VideoEvalReport(
        clips=len(entries), analyzed=len(results), vision_model=vision, activity=activity, categorical=categorical,
        roles=roles, quality=scalar("quality", "quality_score"), energy=scalar("energy", "energy_score"),
        quality_consistency=consistency_result, failures=tuple(failures), worst=tuple(w for _, w in worst[:worst_n]),
    )


def summarize(report: VideoEvalReport) -> str:
    lines = [f"clips {report.clips}, analyzed {report.analyzed}, vision {report.vision_model or 'none'}"]
    a = report.activity
    lines.append(f"activity: n={a['n']} hit={a['hit_rate']} recall={a['recall']} precision={a['precision']}")
    for k, v in report.categorical.items():
        lines.append(f"{k}: n={v.n} accuracy={v.accuracy}")
    lines.append(f"roles: n={report.roles['n']} top1={report.roles['top1']} top3={report.roles['top3']}")
    lines.append(f"quality: n={report.quality.n} spearman={report.quality.spearman} mae={report.quality.mean_abs_error}")
    lines.append(f"energy: n={report.energy.n} spearman={report.energy.spearman} mae={report.energy.mean_abs_error}")
    if report.quality_consistency:
        lines.append(f"quality re-encode consistency: mae={report.quality_consistency.mean_abs_error} "
                     f"spearman={report.quality_consistency.spearman}")
    lines.append(f"failures: {Counter(f.kind for f in report.failures)}")
    return "\n".join(lines)
