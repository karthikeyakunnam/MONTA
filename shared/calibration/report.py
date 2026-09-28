"""
MONTA — Calibration Report
============================
``build_report(store)`` → per (component, field): reliability curve, ECE, MCE,
Brier, histogram, plus the same statistics *after* applying a calibrator
(held-out when ``holdout`` > 0, so the improvement is not in-sample).

CLI::

    python -m shared.calibration.report --dsn sqlite+aiosqlite:///cal.db --out calibration_report.json
"""

import argparse
import asyncio
import json
import random

from shared.calibration.fit import fit_calibration
from shared.calibration.metrics import reliability_report
from shared.calibration.store import CalibrationStore, SqlCalibrationStore, group_by_field

ECE_TARGET = 0.05


async def build_report(store: CalibrationStore, *, holdout: float = 0.3, seed: int = 0) -> dict:
    groups = group_by_field(await store.labeled())
    out = {"target_ece": ECE_TARGET, "fields": {}}
    rng = random.Random(seed)
    for (component, field), rows in sorted(groups.items()):
        rows = sorted(rows, key=lambda r: r.decision.decision_id)
        rng.shuffle(rows)
        cut = int(len(rows) * (1 - holdout)) if holdout and len(rows) >= 10 else len(rows)
        train, test = rows[:cut], rows[cut:] or rows
        raw = reliability_report([r.decision.raw_confidence for r in test], [r.outcome.correct for r in test],
                                 [r.weight for r in test])
        cmap = fit_calibration(component, field, [r.decision.raw_confidence for r in train],
                               [r.outcome.correct for r in train], [r.weight for r in train])
        calibrated = reliability_report([cmap.apply(r.decision.raw_confidence) for r in test],
                                        [r.outcome.correct for r in test], [r.weight for r in test])
        out["fields"][f"{component}.{field}"] = {
            "n": len(rows), "method": cmap.method, "held_out": len(test) if test is not rows else 0,
            "raw": raw.model_dump(), "calibrated": calibrated.model_dump(),
            "meets_target": calibrated.ece <= ECE_TARGET,
        }
    return out


async def _main(dsn: str, out_path: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(dsn)
    report = await build_report(SqlCalibrationStore(engine))
    await engine.dispose()
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    for name, r in report["fields"].items():
        print(f"{name:32} n={r['n']:6} ECE raw={r['raw']['ece']:.3f} → {r['calibrated']['ece']:.3f} ({r['method']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", default="calibration_report.json")
    args = ap.parse_args()
    asyncio.run(_main(args.dsn, args.out))
