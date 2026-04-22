"""Baseline evaluation runner for the RAG pipeline.

Loads `scrapes/eval/queries.yaml`, runs each query through `rag_query`, and
records whether any expected URL appears in the retrieved sources. Writes a
JSON report to `scrapes/eval/results/<phase>_<date>.json`.

Usage:
    uv run python scrapes/eval/run_eval.py --phase phase1
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import django
import yaml


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent
RESULTS_DIR = BASE_DIR / "results"
QUERIES_PATH = BASE_DIR / "queries.yaml"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _normalize(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


def _load_queries() -> list[dict[str, Any]]:
    with QUERIES_PATH.open() as f:
        data = yaml.safe_load(f)
    return data["queries"]


def _evaluate_one(entry: dict[str, Any], top_k: int) -> dict[str, Any]:
    from scrapes.rag import rag_query

    expected = {_normalize(u) for u in entry.get("expected_urls", [])}
    t0 = time.perf_counter()
    result = rag_query(entry["query"], top_k=top_k)
    latency_ms = (time.perf_counter() - t0) * 1000

    retrieved = [
        {
            "url": s["website_url"],
            "similarity": s["similarity_score"],
        }
        for s in result["sources"]
    ]
    hit_positions = [
        i for i, s in enumerate(retrieved) if _normalize(s["url"]) in expected
    ]
    top1_hit = 0 in hit_positions
    topk_hit = bool(hit_positions)
    top1_similarity = retrieved[0]["similarity"] if retrieved else None

    return {
        "id": entry["id"],
        "query": entry["query"],
        "tags": entry.get("tags", []),
        "expected_urls": sorted(expected),
        "retrieved": retrieved,
        "hit_positions": hit_positions,
        "recall_at_1": top1_hit,
        "recall_at_k": topk_hit,
        "top1_similarity": top1_similarity,
        "latency_ms": round(latency_ms, 1),
        "answer": result["answer"],
    }


def _summarize(entries: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    n = len(entries)
    recall1 = sum(e["recall_at_1"] for e in entries) / n if n else 0.0
    recallk = sum(e["recall_at_k"] for e in entries) / n if n else 0.0
    sims = [e["top1_similarity"] for e in entries if e["top1_similarity"] is not None]
    mean_top1 = statistics.fmean(sims) if sims else None

    by_tag: dict[str, dict[str, float]] = {}
    for e in entries:
        for tag in e["tags"]:
            bucket = by_tag.setdefault(
                tag, {"count": 0, "recall_at_1": 0, "recall_at_k": 0}
            )
            bucket["count"] += 1
            bucket["recall_at_1"] += int(e["recall_at_1"])
            bucket["recall_at_k"] += int(e["recall_at_k"])
    for tag, bucket in by_tag.items():
        c = bucket["count"]
        bucket["recall_at_1"] = round(bucket["recall_at_1"] / c, 3)
        bucket["recall_at_k"] = round(bucket["recall_at_k"] / c, 3)

    return {
        "n": n,
        "top_k": top_k,
        "recall_at_1": round(recall1, 3),
        "recall_at_k": round(recallk, 3),
        "mean_top1_similarity": round(mean_top1, 4) if mean_top1 is not None else None,
        "by_tag": by_tag,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG eval suite")
    parser.add_argument(
        "--phase", default="phase1", help="Label used in output filename"
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: scrapes/eval/results/<phase>_<YYYY-MM-DD>.json)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated query ids to run (default: all)",
    )
    args = parser.parse_args()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    django.setup()

    queries = _load_queries()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        queries = [q for q in queries if q["id"] in wanted]
        if not queries:
            print(f"No queries matched --only={args.only}", file=sys.stderr)
            return 2

    print(f"Running {len(queries)} queries (top_k={args.top_k})...")
    entries: list[dict[str, Any]] = []
    for i, entry in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] {entry['id']}: {entry['query'][:60]}")
        try:
            entries.append(_evaluate_one(entry, top_k=args.top_k))
        except Exception as e:  # noqa: BLE001 — surface any failure, continue the batch
            print(f"    ERROR: {e}", file=sys.stderr)
            entries.append(
                {
                    "id": entry["id"],
                    "query": entry["query"],
                    "tags": entry.get("tags", []),
                    "error": str(e),
                    "recall_at_1": False,
                    "recall_at_k": False,
                    "top1_similarity": None,
                }
            )

    summary = _summarize([e for e in entries if "error" not in e], top_k=args.top_k)

    out_path = (
        Path(args.out)
        if args.out
        else RESULTS_DIR / f"{args.phase}_{date.today().isoformat()}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": args.phase,
        "date": date.today().isoformat(),
        "summary": summary,
        "entries": entries,
    }
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print("\n=== Summary ===")
    print(f"  recall@1: {summary['recall_at_1']}")
    print(f"  recall@{summary['top_k']}: {summary['recall_at_k']}")
    print(f"  mean top-1 similarity: {summary['mean_top1_similarity']}")
    print(f"  wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
