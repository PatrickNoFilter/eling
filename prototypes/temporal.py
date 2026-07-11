"""Prototype 2: Temporal queries + NLP date parsing for eling.

Inspired by Memvid's Time Index + temporal_track feature.
Adds chronological queries and natural language date filters to fact search.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

# ── Simple NLP Date Parser ──────────────────────────────────────────────────

_TimeHandler = Callable[[re.Match], tuple[str, int]]

# Relative time patterns (English + Indonesian)
_RELATIVE_PATTERNS: list[tuple[re.Pattern, _TimeHandler]] = [
    # "last N hours/days/weeks/months"
    (
        re.compile(r"(?i)last\s+(\d+)\s*(hour|hours|h|jam|menit|minute|minutes|min)\b"),
        lambda m: ("hours", int(m.group(1))),
    ),  # noqa: E731
    (
        re.compile(r"(?i)last\s+(\d+)\s*(day|days|hari|d)\b"),
        lambda m: ("days", int(m.group(1))),
    ),  # noqa: E731
    (
        re.compile(r"(?i)last\s+(\d+)\s*(week|weeks|minggu|w)\b"),
        lambda m: ("weeks", int(m.group(1))),
    ),  # noqa: E731
    (
        re.compile(r"(?i)last\s+(\d+)\s*(month|months|bulan|mo)\b"),
        lambda m: ("months", int(m.group(1))),
    ),  # noqa: E731
    (
        re.compile(r"(?i)last\s+(\d+)\s*(year|years|tahun|y)\b"),
        lambda m: ("years", int(m.group(1))),
    ),  # noqa: E731
    # "yesterday", "kemarin", "today", "hari ini"
    (re.compile(r"(?i)\byesterday\b|\bkemarin\b"), lambda _: ("days", 1)),  # noqa: E731
    (re.compile(r"(?i)\btoday\b|\bhari\s*ini\b|\bsekarang\b"), lambda _: ("days", 0)),  # noqa: E731
    # "this week/month/year"
    (re.compile(r"(?i)\bthis\s*(week|minggu)\b"), lambda _: ("this_week", 0)),  # noqa: E731
    (re.compile(r"(?i)\bthis\s*(month|bulan)\b"), lambda _: ("this_month", 0)),  # noqa: E731
    (re.compile(r"(?i)\bthis\s*(year|tahun)\b"), lambda _: ("this_year", 0)),  # noqa: E731
    # "past N days" / "in the last N hours"
    (
        re.compile(
            r"(?i)(?:past|in the last)\s+(\d+)\s*(hours?|h|days?|hari|weeks?|minggu)\b"
        ),
        lambda m: (
            m.group(2).lower() + ("s" if not m.group(2).endswith("s") else ""),
            int(m.group(1)),
        ),
    ),  # noqa: E731
]


def parse_time_range(query: str) -> tuple[float | None, float | None]:
    """Extract time range from a natural language query.

    Returns (start_timestamp, end_timestamp) as Unix floats.
    None means unbounded.
    """
    now = time.time()
    # Default unbounded
    start = None
    end = None

    for pattern, handler in _RELATIVE_PATTERNS:
        m = pattern.search(query)
        if m:
            unit, amount = handler(m)
            td: timedelta | None = None

            if unit in ("hours", "h", "jam", "menit", "minute", "minutes", "min"):
                td = timedelta(hours=amount)
            elif unit in ("days", "d", "hari"):
                td = timedelta(days=amount)
            elif unit in ("weeks", "w", "minggu"):
                td = timedelta(weeks=amount)
            elif unit in ("months", "mo", "bulan"):
                td = timedelta(days=amount * 30)
            elif unit in ("years", "y", "tahun"):
                td = timedelta(days=amount * 365)
            elif unit == "this_week":
                # Start of current week (Monday)
                d = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                start = (d - timedelta(days=d.weekday())).timestamp()
                end = now
            elif unit == "this_month":
                d = datetime.now(timezone.utc).replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                start = d.timestamp()
                end = now
            elif unit == "this_year":
                d = datetime.now(timezone.utc).replace(
                    month=1, day=1, hour=0, minute=0, second=0, microsecond=0
                )
                start = d.timestamp()
                end = now

            if td is not None:
                start = now - td.total_seconds()
                end = now

            if start is not None:
                break  # First match wins

    return (start, end)


def make_temporal_filter_clause(
    time_start: float | None = None,
    time_end: float | None = None,
    column: str = "created_at",
) -> tuple[str, list[Any]]:
    """Build SQL WHERE clause for temporal filtering.

    Returns (clause, params) for use in SQL queries.
    `column` must be a valid timestamp-compatible column.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if time_start is not None:
        clauses.append(f"CAST(strftime('%s', {column}) AS REAL) >= ?")
        ts = datetime.fromtimestamp(time_start, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        params.append(ts)
    if time_end is not None:
        clauses.append(f"CAST(strftime('%s', {column}) AS REAL) <= ?")
        ts = datetime.fromtimestamp(time_end, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        params.append(ts)

    if not clauses:
        return ("", [])

    return (" AND " + " AND ".join(clauses), params)


def has_temporal_intent(query: str) -> bool:
    """Check if a query has any temporal intent, to decide whether to apply filters."""
    temporal_indicators = [
        r"(?i)\b(yesterday|today|last|past|this\s+(week|month|year|hari|minggu|bulan|tahun))\b",
        r"(?i)\b(kemarin|hari\s*ini|minggu\s+lalu|bulan\s+lalu)\b",
        r"(?i)\bin the (last|past)\b",
        r"\b\d+\s*(hours?|days?|weeks?|months?|years?|h|hari|jam|menit|minggu|bulan|tahun)\b",
    ]
    for pat in temporal_indicators:
        if re.search(pat, query):
            return True
    return False


# ── Temporal Augmentation for Search Results ────────────────────────────────


def augment_with_temporal_facts(
    results: list[dict],
    time_start: float | None,
    time_end: float | None,
) -> list[dict]:
    """Tag search results with temporal metadata for richer responses."""
    augmented = []
    now = time.time()

    for r in results:
        r = dict(r)  # don't mutate original
        created = r.get("created_at", "")
        if created and isinstance(created, str):
            try:
                ts = datetime.strptime(created, "%Y-%m-%d %H:%M:%S").timestamp()
                age_hours = (now - ts) / 3600
                if age_hours < 1:
                    r["_temporal"] = "moments_ago"
                elif age_hours < 24:
                    r["_temporal"] = "today"
                elif age_hours < 48:
                    r["_temporal"] = "yesterday"
                elif age_hours < 168:  # 7 days
                    r["_temporal"] = "this_week"
                elif age_hours < 720:  # 30 days
                    r["_temporal"] = "this_month"
                else:
                    r["_temporal"] = "older"
            except (ValueError, TypeError):
                r["_temporal"] = "unknown"
        else:
            r["_temporal"] = "unknown"

        # Mark if this result matched temporal filter
        if time_start is not None or time_end is not None:
            r["_temporal_filtered"] = True

        augmented.append(r)

    return augmented


# ── Time-Aware Fact Search (integration with FactsLayer) ────────────────────


def search_with_time(
    facts_layer: Any,
    query: str,
    time_start: float | None = None,
    time_end: float | None = None,
    **kwargs,
) -> list[dict]:
    """Search facts with temporal filtering.

    Extends the existing FactsLayer.search() with temporal bounds.
    Falls back to normal search if no temporal parameters.
    """
    # If query has temporal intent but no explicit time bounds, parse them
    if (
        time_start is None
        and time_end is None
        and has_temporal_intent(query)
        and query.strip()
    ):
        time_start, time_end = parse_time_range(query)
        # Strip temporal tokens from query for better BM25 matching
        clean_query = _strip_temporal_tokens(query)
        if clean_query.strip():
            query = clean_query

    results = facts_layer.search(query, **kwargs)

    if time_start is not None or time_end is not None:
        results = augment_with_temporal_facts(results, time_start, time_end)

    return results


def _strip_temporal_tokens(query: str) -> str:
    """Remove temporal language from query for cleaner BM25 matching."""
    temporal_words = (
        r"(?i)\b(yesterday|today|last|past|this\s+week|this\s+month|this\s+year|"
        r"kemarin|hari\s+ini|minggu\s+lalu|bulan\s+lalu|sekarang)\b"
    )
    cleaned = re.sub(temporal_words, "", query)
    return " ".join(cleaned.split())
