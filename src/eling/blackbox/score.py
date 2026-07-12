"""Context-efficiency scoring engine — 11 metrics + per-archetype profiling.

Port of the Agent-Blackbox efficiency analysis (MIT, Taewoo Park)
adapted for Python and extended with eling fact-store integration.
"""

from __future__ import annotations

import math
from collections import Counter

from .core import (
    TraceEvent,
    RunSummary,
    EfficiencyReport,
    EventType,
    TaskArchetype,
)

# ── Thresholds (from Agent-Blackbox analysis.md) ─────────────────────────

PERFECT_SCORE = 100
METRIC_WEIGHTS = {
    TaskArchetype.RESEARCH: {
        "context_pressure": 0.10,
        "cache_hit_ratio": 0.15,
        "redundant_reads": 0.08,
        "read_amplification": 0.08,
        "large_injections": 0.12,
        "retry_waste": 0.12,
        "yield_density": 0.10,
        "tool_overhead": 0.05,
        "edit_churn": 0.05,
        "large_file_reads": 0.05,
        "unused_reads": 0.10,
    },
    TaskArchetype.DEBUG: {
        "context_pressure": 0.08,
        "cache_hit_ratio": 0.10,
        "redundant_reads": 0.12,
        "read_amplification": 0.10,
        "large_injections": 0.10,
        "retry_waste": 0.15,
        "yield_density": 0.10,
        "tool_overhead": 0.08,
        "edit_churn": 0.07,
        "large_file_reads": 0.05,
        "unused_reads": 0.05,
    },
    TaskArchetype.OPS: {
        "context_pressure": 0.12,
        "cache_hit_ratio": 0.12,
        "redundant_reads": 0.08,
        "read_amplification": 0.05,
        "large_injections": 0.10,
        "retry_waste": 0.20,
        "yield_density": 0.08,
        "tool_overhead": 0.10,
        "edit_churn": 0.05,
        "large_file_reads": 0.05,
        "unused_reads": 0.05,
    },
    TaskArchetype.FEATURE: {
        "context_pressure": 0.10,
        "cache_hit_ratio": 0.10,
        "redundant_reads": 0.10,
        "read_amplification": 0.12,
        "large_injections": 0.08,
        "retry_waste": 0.08,
        "yield_density": 0.12,
        "tool_overhead": 0.08,
        "edit_churn": 0.10,
        "large_file_reads": 0.06,
        "unused_reads": 0.06,
    },
    TaskArchetype.EDIT: {
        "context_pressure": 0.10,
        "cache_hit_ratio": 0.12,
        "redundant_reads": 0.12,
        "read_amplification": 0.12,
        "large_injections": 0.08,
        "retry_waste": 0.08,
        "yield_density": 0.10,
        "tool_overhead": 0.08,
        "edit_churn": 0.10,
        "large_file_reads": 0.05,
        "unused_reads": 0.05,
    },
    TaskArchetype.UNKNOWN: {
        "context_pressure": 0.10,
        "cache_hit_ratio": 0.10,
        "redundant_reads": 0.10,
        "read_amplification": 0.10,
        "large_injections": 0.10,
        "retry_waste": 0.10,
        "yield_density": 0.10,
        "tool_overhead": 0.10,
        "edit_churn": 0.10,
        "large_file_reads": 0.10,
        "unused_reads": 0.10,
    },
}

ARCHETYPE_DEFAULTS = {
    TaskArchetype.UNKNOWN: {
        "ideal_reads_per_edit": 3.0,
        "ideal_yield_per_1k": 1.5,
        "retry_threshold": 2,
        "max_context_pressure": 80000,
    },
    TaskArchetype.RESEARCH: {
        "ideal_reads_per_edit": 8.0,
        "ideal_yield_per_1k": 0.5,
        "retry_threshold": 3,
        "max_context_pressure": 120000,
    },
    TaskArchetype.DEBUG: {
        "ideal_reads_per_edit": 5.0,
        "ideal_yield_per_1k": 1.0,
        "retry_threshold": 4,
        "max_context_pressure": 100000,
    },
    TaskArchetype.OPS: {
        "ideal_reads_per_edit": 2.0,
        "ideal_yield_per_1k": 2.0,
        "retry_threshold": 2,
        "max_context_pressure": 60000,
    },
    TaskArchetype.FEATURE: {
        "ideal_reads_per_edit": 3.0,
        "ideal_yield_per_1k": 1.5,
        "retry_threshold": 2,
        "max_context_pressure": 80000,
    },
    TaskArchetype.EDIT: {
        "ideal_reads_per_edit": 2.0,
        "ideal_yield_per_1k": 2.5,
        "retry_threshold": 2,
        "max_context_pressure": 60000,
    },
}


def _score_sigmoid(value: float, midpoint: float, steepness: float = 0.1) -> float:
    """Map a metric to a 0-1 score using a sigmoid centered at midpoint."""
    return 1.0 / (1.0 + math.exp((value - midpoint) * steepness))


def _score_inverse(value: float, max_good: float) -> float:
    """Score decreases linearly from 1.0 at 0 to 0.0 at max_good."""
    return max(0.0, 1.0 - (value / max_good))


def _score_ratio(ratio: float, ideal: float = 1.0) -> float:
    """Score based on how close ratio is to ideal."""
    if ratio <= 0:
        return 1.0
    deviation = abs(ratio - ideal) / ideal
    return max(0.0, 1.0 - deviation)


class EfficiencyScorer:
    """Computes 11-metric context-efficiency scores from a run's events."""

    def __init__(self, archetype: TaskArchetype = TaskArchetype.UNKNOWN):
        self.archetype = archetype
        self._cfg = ARCHETYPE_DEFAULTS.get(
            archetype, ARCHETYPE_DEFAULTS[TaskArchetype.UNKNOWN]
        )
        self._weights = METRIC_WEIGHTS.get(
            archetype, METRIC_WEIGHTS[TaskArchetype.UNKNOWN]
        )

    def score(self, events: list[TraceEvent], summary: RunSummary) -> EfficiencyReport:
        """Compute full 11-metric report from raw events."""
        report = EfficiencyReport(run_id=summary.run_id, archetype=self.archetype)

        # 1. Context pressure — peak context size
        report.context_pressure = self._score_context_pressure(events)

        # 2. Cache hit ratio — KV-cache efficiency
        report.cache_hit_ratio = self._score_cache_hit(events)

        # 3. Redundant reads — same file read >1 without intervening edit
        (
            report.redundant_reads,
            report.reclaimable_tokens["redundant_reads"],
            report.offenders["redundant_reads"],
        ) = self._score_redundant_reads(events)

        # 4. Read amplification — bytes read vs bytes written
        (
            report.read_amplification,
            report.reclaimable_tokens["read_amplification"],
            report.offenders["read_amplification"],
        ) = self._score_read_amplification(events, summary)

        # 5. Large injections — single tool output flooding context
        (
            report.large_injections,
            report.reclaimable_tokens["large_injections"],
            report.offenders["large_injections"],
        ) = self._score_large_injections(events)

        # 6. Retry waste — failing commands re-run before fix
        (
            report.retry_waste,
            report.reclaimable_tokens["retry_waste"],
            report.offenders["retry_waste"],
        ) = self._score_retry_waste(events)

        # 7. Yield density — concrete change per 1k tokens
        report.yield_density = self._score_yield_density(summary)

        # 8. Tool overhead — tool calls relative to outcomes
        report.tool_overhead = self._score_tool_overhead(summary)

        # 9. Edit churn — same file rewritten many times
        (
            report.edit_churn,
            report.reclaimable_tokens["edit_churn"],
            report.offenders["edit_churn"],
        ) = self._score_edit_churn(events)

        # 10. Large file reads — oversized files read whole
        (
            report.large_file_reads,
            report.reclaimable_tokens["large_file_reads"],
            report.offenders["large_file_reads"],
        ) = self._score_large_file_reads(summary)

        # 11. Unused reads — read but never edited files
        (
            report.unused_reads,
            report.reclaimable_tokens["unused_reads"],
            report.offenders["unused_reads"],
        ) = self._score_unused_reads(summary)

        # Composite
        report.overall_score = self._compute_composite(report)
        return report

    def _compute_composite(self, report: EfficiencyReport) -> float:
        score = 0.0
        for metric, weight in self._weights.items():
            val = getattr(report, metric, 0.5)
            score += val * weight
        return round(score * PERFECT_SCORE, 1)

    def _score_context_pressure(self, events: list[TraceEvent]) -> float:
        peak = max(
            (e.context_tokens or 0 for e in events if e.context_tokens is not None),
            default=0,
        )
        max_ctx = self._cfg["max_context_pressure"]
        return _score_inverse(peak, max_ctx * 1.5)

    def _score_cache_hit(self, events: list[TraceEvent]) -> float:
        total_ctx = sum(e.context_tokens or 0 for e in events if e.context_tokens)
        total_cache = sum(e.cache_tokens or 0 for e in events if e.cache_tokens)
        if total_ctx == 0:
            return 0.5
        ratio = total_cache / total_ctx
        return _score_ratio(ratio, ideal=0.7)

    def _score_redundant_reads(
        self, events: list[TraceEvent]
    ) -> tuple[float, int, list[str]]:
        """Detect files read more than once without an edit in between."""
        file_reads: dict[str, int] = Counter()
        file_edits: set[str] = set()
        for ev in events:
            if ev.type == EventType.READ_FILE and ev.file_path:
                file_reads[ev.file_path] += 1
            if ev.type == EventType.EDIT_FILE and ev.file_path:
                file_edits.add(ev.file_path)

        redundant: list[tuple[str, int]] = []
        for f, count in file_reads.items():
            if f not in file_edits and count > 1:
                redundant.append((f, count - 1))
            elif f in file_edits and count > 2:
                redundant.append((f, count - 2))  # 1 read per edit is expected

        if not file_reads:
            return 1.0, 0, []

        total_redundant = sum(c for _, c in redundant)
        total_reads = sum(file_reads.values())
        offenders = [f"{f} x{c}" for f, c in redundant[:10]]
        reclaimable = total_redundant * 2000  # ~2k tokens per redundant read
        score = _score_inverse(total_redundant, max(total_reads * 0.3, 3))
        return score, reclaimable, offenders

    def _score_read_amplification(
        self,
        events: list[TraceEvent],
        summary: RunSummary,
    ) -> tuple[float, int, list[str]]:
        read_bytes = sum(
            e.input_size or 0 for e in events if e.type == EventType.READ_FILE
        )
        written_bytes = sum(
            e.input_size or 0
            for e in events
            if e.type in (EventType.WRITE_FILE, EventType.EDIT_FILE)
        )
        if written_bytes == 0:
            return 1.0, 0, []
        ratio = read_bytes / written_bytes
        ideal = self._cfg["ideal_reads_per_edit"]
        score = _score_inverse(ratio, ideal * 10)
        reclaimable = max(0, int(read_bytes - written_bytes * ideal * 2))
        offenders = [f"{f}" for f in sorted(summary.files_read)[:5]]
        return score, reclaimable, offenders

    def _score_large_injections(
        self, events: list[TraceEvent]
    ) -> tuple[float, int, list[str]]:
        LARGE_THRESHOLD = 4000  # tokens
        large: list[int] = []
        for ev in events:
            if ev.output_size and ev.output_size > LARGE_THRESHOLD:
                large.append(ev.output_size)
        if not large:
            return 1.0, 0, []
        total_waste = sum(max(0, s - LARGE_THRESHOLD) for s in large)
        reclaimable = min(total_waste, int(total_waste * 0.3))
        score = _score_inverse(len(large), 10)
        return (
            score,
            reclaimable,
            [f"+{s // 1000}k" for s in sorted(large, reverse=True)[:5]],
        )

    def _score_retry_waste(
        self, events: list[TraceEvent]
    ) -> tuple[float, int, list[str]]:
        """Detect same command failing multiple times in a row."""
        cmd_seq: list[str] = []
        for ev in events:
            if ev.type == EventType.BASH and ev.command:
                cmd_seq.append(ev.command)

        retries: list[tuple[str, int]] = []
        seen: dict[str, int] = {}
        for cmd in cmd_seq:
            seen[cmd] = seen.get(cmd, 0) + 1

        for cmd, count in seen.items():
            threshold = self._cfg["retry_threshold"]
            if count > threshold:
                retries.append((cmd, count - threshold))

        if not retries:
            return 1.0, 0, []

        total_retries = sum(c for _, c in retries)
        reclaimable = total_retries * 1500
        threshold = self._cfg["retry_threshold"]
        score = _score_inverse(total_retries, threshold * 3)
        return (
            score,
            reclaimable,
            [
                f"{c.split()[0] if ' ' in c else c[:20]} x{cnt}"
                for cnt, (c, cnt) in enumerate(retries[:5])
            ],
        )

    def _score_yield_density(self, summary: RunSummary) -> float:
        total_tokens = summary.total_tokens or 1
        total_edits = summary.edit_count + summary.write_count
        yield_per_1k = (total_edits * 1000) / total_tokens
        ideal = self._cfg["ideal_yield_per_1k"]
        return _score_ratio(yield_per_1k, ideal=ideal)

    def _score_tool_overhead(self, summary: RunSummary) -> float:
        total_calls = summary.tool_call_count + summary.bash_count
        outcomes = summary.edit_count + summary.test_count + summary.commit_count
        if total_calls == 0:
            return 1.0
        ratio = outcomes / total_calls if total_calls > 0 else 0
        return _score_inverse(1.0 / (ratio + 0.01), max_good=5.0)

    def _score_edit_churn(
        self, events: list[TraceEvent]
    ) -> tuple[float, int, list[str]]:
        file_edits: Counter[str] = Counter()
        file_writes: Counter[str] = Counter()
        for ev in events:
            if ev.type == EventType.EDIT_FILE and ev.file_path:
                file_edits[ev.file_path] += 1
            if ev.type == EventType.WRITE_FILE and ev.file_path:
                file_writes[ev.file_path] += 1

        churned: list[tuple[str, int]] = []
        for f, c in file_edits.items():
            if c > 3:
                churned.append((f, c - 3))

        if not churned:
            return 1.0, 0, []

        total_churn = sum(c for _, c in churned)
        reclaimable = total_churn * 2500
        score = _score_inverse(total_churn, 15)
        return score, reclaimable, [f"{f} x{c}" for f, c in churned[:5]]

    def _score_large_file_reads(
        self, summary: RunSummary
    ) -> tuple[float, int, list[str]]:
        # Normal summary doesn't track per-file sizes; estimate from count
        file_count = len(summary.files_read)
        if file_count <= 5:
            return 1.0, 0, []
        large_estimate = max(0, file_count - 5)
        score = _score_inverse(large_estimate, 20)
        reclaimable = large_estimate * 3000
        offenders = sorted(summary.files_read)[:3]
        return score, reclaimable, [f for f in offenders]

    def _score_unused_reads(self, summary: RunSummary) -> tuple[float, int, list[str]]:
        if not summary.files_read:
            return 1.0, 0, []
        unused = summary.files_read - summary.files_written
        if not unused:
            return 1.0, 0, []
        score = _score_inverse(len(unused), len(summary.files_read) * 0.5)
        reclaimable = len(unused) * 2500
        offenders = sorted(unused)[:5]
        return score, reclaimable, offenders

    @staticmethod
    def detect_archetype(
        events: list[TraceEvent], summary: RunSummary
    ) -> TaskArchetype:
        """Heuristic archetype detection from events."""
        edit_ratio = summary.edit_count / max(summary.read_count, 1)
        test_ratio = summary.test_count / max(summary.bash_count, 1)
        error_ratio = summary.error_count / max(
            summary.bash_count + summary.tool_call_count, 1
        )
        read_wide = len(summary.files_read) > 15 and edit_ratio < 0.1

        if read_wide and error_ratio < 0.1:
            return TaskArchetype.RESEARCH
        if error_ratio > 0.3 and test_ratio > 0.1:
            return TaskArchetype.DEBUG
        if summary.bash_count > summary.edit_count * 5 and summary.edit_count < 3:
            return TaskArchetype.OPS
        if summary.edit_count > 10 and summary.commit_count > 0:
            return TaskArchetype.FEATURE
        if summary.edit_count > 0 and summary.commit_count == 0:
            return TaskArchetype.EDIT
        return TaskArchetype.UNKNOWN
