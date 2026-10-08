"""M5.8D exploratorio de punta a punta (BD LOCAL con 0008; nunca producción). Ver PROTOCOL.md.

    python -m research.m58d.run --database-url <BD local> --out <dir fuera del repo> [--rows-cache <jsonl>]

Ninguna fila con kickoff >= 2026-01-01: el test de M5.8B no se usa para nada.
"""

import argparse
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from research.m58 import metrics
from research.m58.dataset import FEATURE_COLUMNS, SIDES, dataset_fingerprint, leakage_violations
from research.m58.labels import CLASSES
from research.m58.splits import TEST_START

DATETIME_FIELDS = ("kickoff_at", "cutoff", "horizon") + tuple(
    f"{s}_audit_{f}" for s in SIDES for f in ("max_window_kickoff", "max_used_available_at", "max_used_observed_at")
)


def _load(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data["rows"]:
        for f in DATETIME_FIELDS:
            if r.get(f):
                r[f] = datetime.fromisoformat(r[f])
    return data["rows"], Counter(data["excluded"]), data["coverage"]


def coverage_report(rows, coverage):
    by_cause = Counter(c["cause"] for c in coverage)
    total = sum(by_cause.values())
    by_comp_season = defaultdict(Counter)
    by_source = defaultdict(Counter)
    for c in coverage:
        by_comp_season[f"{c['window_competition']} {c['window_season']}"][c["cause"]] += 1
        by_source[",".join(c["observation_sources"]) or "none"][c["cause"]] += 1
    features = {col: round(100 * sum(not r[f"{col}_missing"] for r in rows) / len(rows), 1)
                for col in FEATURE_COLUMNS if not col.endswith("_missing") and col.startswith("home_")}
    informative = defaultdict(lambda: [0, 0])
    for r in rows:
        key = f"{r['competition']} {r['season_year']}"
        informative[key][0] += all(r[f"{s}_n_used"] > 0 for s in SIDES)
        informative[key][1] += 1
    return {
        "window_matches": total,
        "by_cause_pct": {k: round(100 * v / total, 1) for k, v in by_cause.most_common()},
        "by_cause": dict(by_cause),
        "by_window_competition_season": {k: dict(v) for k, v in sorted(by_comp_season.items())},
        "by_observation_source": {k: dict(v) for k, v in sorted(by_source.items())},
        "home_feature_non_missing_pct": features,
        "target_informative_pct_by_competition_season": {k: round(100 * a / b, 1) for k, (a, b) in sorted(informative.items())},
    }


def scores(probs, labels):
    return {"log_loss": round(metrics.log_loss(probs, labels), 4), "brier": round(metrics.brier(probs, labels), 4), "rps": round(metrics.rps(probs, labels), 4)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--database-url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rows-cache")
    args = p.parse_args(argv)
    os.environ["DATABASE_URL"] = args.database_url
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from research.m58d.dataset import build_rows

    cache = Path(args.rows_cache) if args.rows_cache else None
    report = {"protocol": "research/m58d/PROTOCOL.md", "exploratory": True}
    with Session(create_engine(args.database_url)) as db:
        horizon = db.execute(text("select min(observed_at) from fixture_observations")).scalar_one()
        report["horizon"] = str(horizon)
        if cache and cache.exists():
            rows, excluded, coverage = _load(cache)
        else:
            rows, excluded, coverage = build_rows(db, horizon)
            if cache:
                cache.write_text(json.dumps({"rows": rows, "excluded": excluded, "coverage": coverage}, default=str), encoding="utf-8")
        db.rollback()
    assert all(r["kickoff_at"] < TEST_START for r in rows), "fila del periodo de test de M5.8B"
    report["dataset"] = {
        "rows": len(rows), "excluded": dict(excluded), "fingerprint": dataset_fingerprint(rows),
        "class_balance": metrics.class_balance([r["label"] for r in rows]),
        "extra_time_labels": dict(Counter(r["label"] for r in rows if r["label_status"] in ("AET", "PEN"))),
        "extra_time_non_draw_at_90": sum(r["extra_time_non_draw_at_90"] for r in rows),
        "quality_flags": dict(Counter(f for r in rows for f in r["quality_flags"])),
        "kickoff_range": [str(min(r["kickoff_at"] for r in rows)), str(max(r["kickoff_at"] for r in rows))],
    }
    report["leakage_violations"] = sum(len(leakage_violations(r)) for r in rows)
    report["coverage"] = coverage_report(rows, coverage)
    if report["leakage_violations"]:
        report["status"] = "BLOCKED_LEAKAGE"
        return _write(out, report)

    from research.m58 import models as m58
    from research.m58d import models as m58d
    from research.m58d.folds import ORIGINS, fold

    pooled = defaultdict(list)  # modelo -> [(fila, probs)]
    report["folds"] = []
    for origin in ORIGINS:
        f = fold(rows, origin)
        info = {"origin": str(origin.date()), **{k: len(v) for k, v in f.items()}}
        fit, early, cal, ev = f["fit"], f["early"], f["calibration"], f["evaluation"]
        cal_labels, ev_labels = [r["label"] for r in cal], [r["label"] for r in ev]
        alpha, _ = m58.select_alpha(early, cal)
        c, c_scores = m58.select_c(early, cal)
        lam, lam_scores = m58d.select_lambda(early, cal)
        early_b2 = m58.LogisticB2(c).fit(early)
        calibrator = m58d.VectorCalibrator().fit(early_b2.predict(cal), cal_labels)
        preds = {
            "B0": m58.ClassFrequency().fit(fit).predict(ev),
            "B1": m58.CompetitionFrequency(alpha).fit(fit).predict(ev),
            "B2*": m58.LogisticB2(c).fit(fit).predict(ev),
            "B2early": early_b2.predict(ev),
            "B2early+cal": calibrator.transform(early_b2.predict(ev)),
            "ORD": m58d.OrdinalLogit(lam).fit(fit).predict(ev),
        }
        for c_grid in m58.C_GRID:  # efecto de la regularización: se registran todos (no es selección)
            preds[f"B2(C={c_grid})"] = m58.LogisticB2(c_grid).fit(fit).predict(ev)
        info.update({"alpha": alpha, "C": c, "C_scores_cal": {str(k): round(v, 4) for k, v in c_scores.items()}, "lambda": lam,
                     "lambda_scores_cal": {str(k): round(v, 4) for k, v in lam_scores.items()},
                     "scores": {m: scores(pp, ev_labels) for m, pp in preds.items()}})
        report["folds"].append(info)
        for m, pp in preds.items():
            pooled[m].extend(zip(ev, pp))

    labels = [r["label"] for r, _ in pooled["B0"]]
    summary = {}
    for m, pairs in pooled.items():
        probs = [pp for _, pp in pairs]
        tagged = [dict(r, p=pp) for r, pp in pairs]
        ll = metrics.block_bootstrap(tagged, lambda rs: metrics.log_loss([r["p"] for r in rs], [r["label"] for r in rs]), n_boot=500)
        p_draw = [pp[1] for pp in probs]
        q = statistics.quantiles(p_draw, n=20)
        entry = {**scores(probs, labels), "log_loss_ci95": [round(x, 4) for x in ll],
                 "ece_mean": metrics.calibration(probs, labels)["ece_mean"],
                 "p_draw": {"mean": round(statistics.mean(p_draw), 4), "sd": round(statistics.pstdev(p_draw), 4),
                            "p05": round(q[0], 4), "p50": round(q[9], 4), "p95": round(q[18], 4)}}
        if not m.startswith("B2(C="):
            slopes = {}
            for k, cls in enumerate(CLASSES):
                def slope(rs, cls=cls):
                    return m58.calibration_slopes([r["p"] for r in rs], [r["label"] for r in rs])[cls]["slope"]
                est, lo, hi = metrics.block_bootstrap(tagged, slope, n_boot=200)
                slopes[cls] = [round(est, 3), round(lo, 3), round(hi, 3)]
            entry["calibration_slope_ci95"] = slopes
        summary[m] = entry
    report["pooled_evaluation"] = {"n": len(labels), "class_balance": metrics.class_balance(labels), "models": summary}

    paired = {}
    rows_by_model = {m: {r["fixture_id"]: pp for r, pp in pairs} for m, pairs in pooled.items()}
    base_rows = [r for r, _ in pooled["B0"]]
    for a, b in (("B2*", "B0"), ("B2*", "B1"), ("B2early+cal", "B2early"), ("ORD", "B2*"), ("ORD", "B1"), ("B1", "B0")):
        tagged = [dict(r, pa=rows_by_model[a][r["fixture_id"]], pb=rows_by_model[b][r["fixture_id"]]) for r in base_rows]
        est, lo, hi = metrics.block_bootstrap(tagged, lambda rs: metrics.log_loss([r["pa"] for r in rs], [r["label"] for r in rs])
                                              - metrics.log_loss([r["pb"] for r in rs], [r["label"] for r in rs]), n_boot=500)
        paired[f"{a}-{b}"] = [round(est, 4), round(lo, 4), round(hi, 4)]
    report["paired_log_loss_diff_ci95"] = paired

    sens = defaultdict(lambda: {"n": 0})
    for key_fn, name in ((lambda r: r["competition"], "competition"), (lambda r: str(r["season_year"]), "season")):
        groups = defaultdict(list)
        for r in base_rows:
            groups[key_fn(r)].append(r)
        sens[name] = {g: {"n": len(rs), **{m: round(metrics.log_loss([rows_by_model[m][r["fixture_id"]] for r in rs], [r["label"] for r in rs]), 4)
                                         for m in ("B1", "B2*", "ORD")}} for g, rs in sorted(groups.items()) if len(rs) >= 50}
    report["sensitivity"] = dict(sens)
    report["status"] = "EXPLORATORY_COMPLETED"
    return _write(out, report)


def _write(out: Path, report: dict) -> dict:
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "leakage_violations") if k in report}))
    return report


if __name__ == "__main__":
    main()
