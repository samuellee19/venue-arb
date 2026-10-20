"""Bake-off evaluation: candidate recall of each model's vectors on the truth set, logged to MLflow.

    secrets uv run python -m venue_arb.bakeoff.evaluate [ARTIFACTS [LISTINGS]]    (MLFLOW_TRACKING_URI from mise.toml)

ARTIFACTS (default data/bakeoff/out/artifacts) holds manifest.json and <slug>.npy from jobs/embed_bakeoff.py;
LISTINGS (default data/bakeoff/listings.parquet) is the snapshot the job embedded.

Candidates come from the production `embed_pairs`; recall@k reruns it with min_sim off (max_days and the category
filter stay). `kalshi_hit` = share of truth Kalshi markets with at least one true partner among their candidates.
"""

import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from venue_arb.bakeoff.snapshot import TRUTH
from venue_arb.matching.candidates import embed_pairs

PROD = {"k": 5, "min_sim": 0.75, "max_days": 31}
KS = (1, 5, 10, 25)
NORM_TOL = 1e-3


def recall(truth: list[tuple[str, str, str]], cands: set[tuple[str, str]]) -> dict[str, dict]:
    out = {}
    for g in sorted({t[2] for t in truth}) + ["all"]:
        ts = [t for t in truth if g in ("all", t[2])]
        found = [t for t in ts if (t[0], t[1]) in cands]
        ks = {t[0] for t in ts}
        out[g] = {"pairs": len(ts), "found": len(found), "recall": len(found) / len(ts), "kalshi": len(ks),
                  "kalshi_hit": len({t[0] for t in found}) / len(ks)}
    return out


def split(rows: list[dict], vecs: np.ndarray):
    """(kalshi rows, vectors), (polymarket rows, vectors), keeping Parquet row order."""
    out = []
    for venue in ("kalshi", "polymarket"):
        idx = [i for i, r in enumerate(rows) if r["venue"] == venue]
        out.append(([rows[i] for i in idx], vecs[idx]))
    return out


def vector_stats(v: np.ndarray) -> dict:
    finite = np.isfinite(v).all(axis=1)
    err = np.abs(np.linalg.norm(v[finite], axis=1) - 1)
    return {"rows": v.shape[0], "dim": v.shape[1], "non_finite": int((~finite).sum()),
            "max_norm_err": float(err.max()) if err.size else 0.0}


def _guards(slug: str, v: np.ndarray, spec: dict, n_rows: int, sha: str, manifest_sha: str):
    from qa import checks

    s = vector_stats(v)
    target = f"bakeoff/{slug}.npy"
    return [
        checks.Result("input_sha_match", sha == manifest_sha, detail=f"{sha[:12]} vs {manifest_sha[:12]}",
                      target=target),
        checks.expect_count_match(n_rows, s["rows"], target),
        checks.Result("dim_as_declared", s["dim"] == spec["dim"], s["dim"], spec["dim"], target=target),
        checks.Result("vectors_finite", s["non_finite"] == 0, s["non_finite"], 0, target=target),
        checks.Result("unit_norm", s["max_norm_err"] <= NORM_TOL, s["max_norm_err"], NORM_TOL, target=target),
    ]


def evaluate(rows, vecs, truth) -> tuple[dict, int, dict]:
    (kr, kv), (pr, pv) = split(rows, vecs)
    prod = embed_pairs(kr, pr, kv, pv, **PROD)
    at = {k: recall(truth, {(a, b) for a, b, _ in embed_pairs(kr, pr, kv, pv, k=k, min_sim=-2.0,
                                                               max_days=PROD["max_days"])}) for k in KS}
    return recall(truth, {(a, b) for a, b, _ in prod}), len(prod), at


def table(results: dict) -> str:
    groups = list(next(iter(results.values()))["prod"])
    head = ("| model | group | pairs | recall (prod k=5, min_sim 0.75) | kalshi hit (prod) | "
            + " | ".join(f"recall@{k}" for k in KS) + " | candidates (prod) | embed s (T4) |")
    lines = [head, "|" + "---|" * (head.count("|") - 1)]
    for slug, r in results.items():
        for g in groups:
            p = r["prod"][g]
            lines.append(f"| {slug} | {g} | {p['pairs']} | {p['found']}/{p['pairs']} = {p['recall']:.3f} | "
                         f"{p['kalshi_hit']:.3f} | " + " | ".join(f"{r['at'][k][g]['recall']:.3f}" for k in KS)
                         + f" | {r['candidates']} | {r['embed_seconds']} |")
    return "\n".join(lines)


def main(data: str = "data/bakeoff/out/artifacts",
         listings: str = "data/bakeoff/listings.parquet") -> None:
    import mlflow
    from qa import checks

    d = Path(data)
    manifest = json.loads((d / "manifest.json").read_text())
    sha = hashlib.sha256(Path(listings).read_bytes()).hexdigest()
    rows = pq.read_table(listings).to_pylist()
    with TRUTH.open() as f:
        truth = [(r["kalshi_market_id"], r["pm_market_id"], r["kind"])
                 for r in csv.DictReader(line for line in f if not line.startswith("#"))]
    ids = {(r["venue"], r["market_id"]) for r in rows}
    covered = sum(("kalshi", a) in ids and ("polymarket", b) in ids for a, b, _ in truth)
    pre = [checks.expect_rows(len(truth), str(TRUTH)), checks.expect_join_coverage(covered, len(truth), str(TRUTH),
                                                                                   min_ratio=1.0)]
    vecs = {}
    for spec in manifest["models"]:
        vecs[spec["slug"]] = np.load(d / f"{spec['slug']}.npy")
        pre += _guards(spec["slug"], vecs[spec["slug"]], spec, len(rows), sha, manifest["input_sha256"])
    checks.enforce(pre)

    results = {}
    for spec in manifest["models"]:
        prod, n, at = evaluate(rows, vecs[spec["slug"]], truth)
        results[spec["slug"]] = {"prod": prod, "candidates": n, "at": at, "embed_seconds": spec["embed_seconds"]}
    md = table(results)
    print(md)

    mlflow.set_experiment("venue-arb")
    truth_sha = hashlib.sha256(TRUTH.read_bytes()).hexdigest()
    for spec in manifest["models"]:
        r = results[spec["slug"]]
        with mlflow.start_run(run_name=f"embed-bakeoff-{spec['slug']}") as run:
            mlflow.log_params({"model": spec["id"], "revision": spec["revision"], "dim": spec["dim"],
                               "prompt": spec["prompt"] or "(none)", "dtype": spec["dtype"], **PROD,
                               "recall_ks": ",".join(map(str, KS)), "snapshot_sha256": sha,
                               "truth_sha256": truth_sha, "gpu": manifest["gpu"]})
            m = {"candidates": r["candidates"], "embed_seconds": spec["embed_seconds"],
                 "load_seconds": spec["load_seconds"]}
            for g, p in r["prod"].items():
                m[f"recall_prod_{g}"] = p["recall"]
                m[f"kalshi_hit_prod_{g}"] = p["kalshi_hit"]
                m |= {f"recall_at_{k}_{g}": r["at"][k][g]["recall"] for k in KS}
            mlflow.log_metrics(m)
            mlflow.log_text(md, "results.md")
            mlflow.log_dict(manifest, "manifest.json")
            print(f"mlflow run {spec['slug']}: {run.info.run_id}")


if __name__ == "__main__":
    main(*sys.argv[1:])
