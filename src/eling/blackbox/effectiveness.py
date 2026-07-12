"""Outcome scoring — did the task actually land?

Separate from efficiency: an efficient-but-failed run reads differently
from a wasteful-but-shipped one.
"""

from __future__ import annotations

from .core import TraceEvent, RunSummary, EffectivenessReport


class EffectivenessScorer:
    """Evaluates whether a task successfully landed based on observed signals."""

    def score(
        self, events: list[TraceEvent], summary: RunSummary
    ) -> EffectivenessReport:
        report = EffectivenessReport(run_id=summary.run_id)

        # Gather signals
        report.has_edits = summary.edit_count > 0 or summary.write_count > 0
        report.has_tests_passed = summary.test_count > 0 and summary.test_failed == 0
        report.has_commits = summary.commit_count > 0
        report.has_errors = summary.error_count > 0

        # Count signals
        positive = sum([report.has_edits, report.has_tests_passed, report.has_commits])

        # Compute confidence
        evidence_count = (
            summary.tool_call_count
            + summary.bash_count
            + summary.edit_count
            + summary.test_count
        )
        if evidence_count >= 10:
            report.confidence = "high"
        elif evidence_count >= 4:
            report.confidence = "medium"
        else:
            report.confidence = "low"

        # Compute score
        if evidence_count == 0:
            report.score = 0.0
            report.label = "unclear"
        elif positive >= 2 and not report.has_errors:
            report.score = 100.0
            report.label = "succeeded" if report.confidence == "high" else "likely ok"
        elif positive >= 1 and not report.has_errors:
            report.score = 70.0
            report.label = "likely ok"
        elif positive >= 1 and report.has_errors:
            report.score = 40.0
            report.label = "mixed"
        elif report.has_errors:
            report.score = 15.0
            report.label = "failed"
        else:
            report.score = 10.0
            report.label = "no outcome detected"

        return report
