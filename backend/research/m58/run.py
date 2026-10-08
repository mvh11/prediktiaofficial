"""Experimento M5.8 de punta a punta (offline, sobre una BD LOCAL con 0008; nunca producción).

Uso (desde backend/, con el venv de investigación):
    python -m research.m58.run --database-url <BD local> --out <dir fuera del repo> [--rows-cache <jsonl>]

Orden: dataset -> auditoría de leakage -> splits y tamaños (para si son insuficientes) ->
selección en validación A (alpha, C) y calibración en validación B -> configuración congelada ->
test evaluado UNA vez -> informe JSON. No escribe en la BD.
"""

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from research.m58 import metrics
from research.m58.dataset import SIDES, build_rows, dataset_fingerprint, leakage_violations
from research.m58.labels import CLASSES
from research.m58.protocol import TestGate, config_hash
from research.m58.splits import TEST, TRAIN, VALIDATION, assign_split, validation_halves

MIN_SIZES = {TRAIN: 2000, VALIDATION: 500, TEST: 300}
DATETIME_FIELDS = ("kickoff_at", "cutoff", "horizon", "label_known_at") + tuple(
    f"{s}_audit_{f}" for s in SIDES for f in ("max_window_kickoff", "max_used_available_at", "max_used_observed_at")
)


def _load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        for f in DATETIME_FIELDS:
            if r.get(f):
                r[f] = datetime.fromisoformat(r[f])
        rows.append(r)
    return rows


def _informative(r: dict) -> bool:
    return all(r[f"{s}_n_used"] > 0 for s in SIDES)


def summarize_split(rows):
    labels = [r["label"] for r in rows]
    return {"n": len(rows), "class_balance": metrics.class_balance(labels), "informative_pct": round(100 * sum(map(_informative, rows)) / len(rows), 1) if rows else 0,
            "kickoff_range": [str(min(r["kickoff_at"] for r in rows)), str(max(r["kickoff_at"] for r in rows))] if rows else None,
            "competitions": len({r["competition_id"] for r in rows})}


def scores(probs, labels):
    return {"log_loss": round(metrics.log_loss(probs, labels), 4), "brier": round(metrics.brier(probs, labels), 4), "rps": round(metrics.rps(probs, labels), 4)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rows-cache")
    args = parser.parse_args(argv)
    os.environ["DATABASE_URL"] = args.database_url
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    engine = create_engine(args.database_url)
    report = {}
    cache = Path(args.rows_cache) if args.rows_cache else None
    with Session(engine) as db:
        horizon = db.execute(text("select min(observed_at) from fixture_observations")).scalar_one()
        report["horizon"] = str(horizon)
        if cache and cache.exists():
            rows, excluded = _load(cache), Counter(json.loads((out / "excluded.json").read_text()))
        else:
            rows, excluded = build_rows(db, horizon)
            if cache:
                cache.write_text("\n".join(json.dumps(r, default=str) for r in rows), encoding="utf-8")
                (out / "excluded.json").write_text(json.dumps(excluded))
        db.rollback()
    report["dataset"] = {"rows": len(rows), "excluded": dict(excluded), "fingerprint": dataset_fingerprint(rows),
                         "retro_label_disagreements": sum(not r["audit_retro_label_agrees"] for r in rows),
                         "extra_time_labels": dict(Counter(r["label"] for r in rows if r["label_status"] in ("AET", "PEN"))),
                         "regimes": dict(Counter(r["regime"] for r in rows)), "retrospective_rows": sum(r["is_retrospective"] for r in rows)}
    violations = [(r["fixture_id"], v) for r in rows for v in leakage_violations(r)]
    report["leakage_violations"] = len(violations)
    if violations:
        report["status"] = "BLOCKED_LEAKAGE"
        return _write(out, report)

    split = {}
    for r in rows:
        split.setdefault(assign_split(r["kickoff_at"]), []).append(r)
    report["splits"] = {k: summarize_split(v) for k, v in sorted(split.items())}
    short = {k: (len(split.get(k, [])), n) for k, n in MIN_SIZES.items() if len(split.get(k, [])) < n}
    if short:
        report["status"] = "BLOCKED_INSUFFICIENT_SPLITS"
        report["insufficient"] = short
        return _write(out, report)

    from research.m58 import models  # numpy / scikit-learn: solo en el entorno de investigación

    train, val, test = split[TRAIN], split[VALIDATION], split[TEST]
    val_a, val_b = validation_halves(val)
    alpha, alpha_scores = models.select_alpha(train, val_a)
    c, c_scores = models.select_c(train, val_a)
    b2 = models.LogisticB2(c).fit(train)
    temperature, t_scores = models.select_temperature(b2, val_b)
    b0, b1 = models.ClassFrequency().fit(train), models.CompetitionFrequency(alpha).fit(train)
    val_b_labels = [r["label"] for r in val_b]
    report["validation"] = {
        "val_a": len(val_a), "val_b": len(val_b), "alpha": alpha, "alpha_scores": alpha_scores, "C": c, "C_scores": c_scores,
        "temperature": temperature,
        "val_b_scores": {"B0": scores(b0.predict(val_b), val_b_labels), "B1": scores(b1.predict(val_b), val_b_labels),
                         "B2": scores(b2.predict(val_b), val_b_labels), "B2T": scores(b2.predict(val_b, temperature), val_b_labels)},
    }
    config = {"dataset": report["dataset"]["fingerprint"], "alpha": alpha, "C": c, "temperature": temperature,
              "features": list(models.NUMERIC + models.FLAGS), "train": len(train), "seed": models.SEED}
    report["frozen_config_hash"] = config_hash(config)
    gate = TestGate(config)

    def evaluate():
        labels = [r["label"] for r in test]
        preds = {"B0": b0.predict(test), "B1": b1.predict(test), "B2": b2.predict(test), "B2T": b2.predict(test, temperature)}
        res = {"n": len(test), "scores": {}, "ci95": {}, "paired_diff_ci95": {}, "calibration": {}, "calibration_slopes": {}, "by_competition": {}}
        tagged = [dict(r, **{f"p_{m}": p for m, p in ((m, preds[m][i]) for m in preds)}) for i, r in enumerate(test)]
        for m in preds:
            res["scores"][m] = scores(preds[m], labels)
            est, lo, hi = metrics.block_bootstrap(tagged, lambda rs, m=m: metrics.log_loss([r[f"p_{m}"] for r in rs], [r["label"] for r in rs]))
            res["ci95"][m] = [round(est, 4), round(lo, 4), round(hi, 4)]
            res["calibration"][m] = {k: v for k, v in metrics.calibration(preds[m], labels).items() if k == "ece_mean"} | {
                c: metrics.calibration(preds[m], labels)[c] for c in CLASSES}
            res["calibration_slopes"][m] = models.calibration_slopes(preds[m], labels)
        for a, b in (("B2", "B1"), ("B2", "B0"), ("B1", "B0"), ("B2T", "B1")):
            def diff(rs, a=a, b=b):
                ls = [r["label"] for r in rs]
                return metrics.log_loss([r[f"p_{a}"] for r in rs], ls) - metrics.log_loss([r[f"p_{b}"] for r in rs], ls)
            est, lo, hi = metrics.block_bootstrap(tagged, diff)
            res["paired_diff_ci95"][f"{a}-{b}"] = [round(est, 4), round(lo, 4), round(hi, 4)]
        for comp in sorted({r["competition"] for r in test}):
            idx = [i for i, r in enumerate(test) if r["competition"] == comp]
            ls = [labels[i] for i in idx]
            res["by_competition"][comp] = {"n": len(idx), **{m: round(metrics.log_loss([preds[m][i] for i in idx], ls), 4) for m in ("B0", "B1", "B2")}}
        res["informative_coverage_pct"] = round(100 * sum(map(_informative, test)) / len(test), 1)
        return res

    report["test"] = gate.evaluate(config, evaluate)
    report["status"] = "COMPLETED"
    return _write(out, report)


def _write(out: Path, report: dict) -> dict:
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "leakage_violations") if k in report}))
    return report


if __name__ == "__main__":
    main()
