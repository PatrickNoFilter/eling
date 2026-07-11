#!/usr/bin/env python3
"""Formal benchmark for Eling memory layers (Task 10.2).

Usage:
    python -m eling.scripts.benchmark [--runs N] [--json]
    python src/eling/scripts/benchmark.py [--runs 20] [--json]

Measures p50/p95/p99 latency for each layer's core operations.
Outputs human-readable table to stderr, JSON to stdout (when --json).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from statistics import quantiles

from eling.brain import Brain


def _seed(brain: Brain, n: int = 100):
    """Seed the brain with n facts for realistic benchmark load."""
    for i in range(n):
        brain.remember(
            f"Benchmark fact #{i}: Python is a high-level programming language used for "
            f"web development, data science, and automation.",
            layer="facts",
            category="code",
            source="benchmark",
        )
        brain.remember(
            "FastAPI is a modern Python web framework for building APIs with type hints.",
            layer="facts",
            category="code",
            source="benchmark",
        )
    # Seed KB
    for i in range(n // 10):
        brain.remember(
            f"KB chunk {i}: SQLite is a C-language library that implements a small, fast, "
            f"self-contained, high-reliability, full-featured SQL database engine.",
            layer="kb",
            source="benchmark",
        )
    # Seed entity graph
    brain.remember(
        "[[FastAPI]] uses [[Python]] for [[web development]]",
        layer="facts",
        category="code",
        source="benchmark",
    )


def _timer(label: str, fn, warmup: int, runs: int) -> dict:
    """Run fn *runs* times, return timing stats. Skips warmup rounds."""
    # warmup
    for _ in range(warmup):
        fn()
    # measured runs
    timings: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        timings.append(time.perf_counter() - t0)

    timings.sort()
    n = len(timings)
    avg = sum(timings) / n
    q = quantiles(timings, n=100)  # 100-ile → access p50/p95/p99
    return {
        "operation": label,
        "runs": n,
        "avg_ms": round(avg * 1000, 3),
        "min_ms": round(timings[0] * 1000, 3),
        "max_ms": round(timings[-1] * 1000, 3),
        "p50_ms": round(q[49] * 1000, 3),
        "p95_ms": round(q[94] * 1000, 3),
        "p99_ms": round(q[98] * 1000, 3),
    }


def run_benchmark(runs: int = 20, warmup: int = 3) -> list[dict]:
    results: list[dict] = []

    # Fresh brain per benchmark to avoid stale-state bias
    home = Path(tempfile.mkdtemp())
    brain = Brain(home=home)
    _seed(brain)

    # ── facts ──
    results.append(
        _timer(
            "facts.remember",
            lambda: brain.remember(
                "New benchmark fact for timing", layer="facts", category="code"
            ),
            warmup,
            runs,
        )
    )

    results.append(
        _timer("facts.search", lambda: brain.recall("Python", limit=5), warmup, runs)
    )

    # ── kb ──
    results.append(
        _timer(
            "kb.remember",
            lambda: brain.remember("New KB chunk for timing", layer="kb"),
            warmup,
            runs,
        )
    )

    results.append(
        _timer("kb.search", lambda: brain.recall("SQLite", limit=5), warmup, runs)
    )

    # ── code ──
    results.append(
        _timer(
            "code.search",
            lambda: brain.code.search("benchmark", max_files=5),
            warmup,
            runs,
        )
    )

    # ── stats ──
    results.append(_timer("stats", lambda: brain.stats(), warmup, runs))

    # ── think (synthesis + gap-analysis) ──
    results.append(_timer("think", lambda: brain.think("Python"), warmup, runs))

    # ── export (JSON) ──
    results.append(_timer("export", lambda: brain.export(format="json"), warmup, runs))

    # ── reason (HRR) ──
    results.append(_timer("reason", lambda: brain.reason(["Python"]), warmup, runs))

    brain.close()
    return results


def print_table(results: list[dict]):
    """Print a human-readable table."""
    header = (
        f"{'Operation':<20} {'p50':>10} {'p95':>10} {'p99':>10} {'avg':>10} {'runs':>6}"
    )
    sep = "-" * len(header)
    print(sep, file=sys.stderr)
    print(header, file=sys.stderr)
    print(sep, file=sys.stderr)
    for r in results:
        print(
            f"{r['operation']:<20} {r['p50_ms']:>10.2f} {r['p95_ms']:>10.2f} "
            f"{r['p99_ms']:>10.2f} {r['avg_ms']:>10.2f} {r['runs']:>6}",
            file=sys.stderr,
        )
    print(sep, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Eling benchmark suite")
    parser.add_argument(
        "--runs", type=int, default=20, help="Number of measured runs per operation"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output to stdout (table goes to stderr)",
    )
    args = parser.parse_args()

    results = run_benchmark(runs=args.runs)
    print_table(results)

    if args.json:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
