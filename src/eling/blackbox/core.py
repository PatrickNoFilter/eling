"""Canonical telemetry event model for agent flight recording.

Mirrors the Agent-Blackbox TraceEvent model (MIT, Taewoo Park) adapted
for Python and extended for Hermes/Zero event shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Canonical event types observed across all agent hosts."""

    # Session lifecycle
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # Agent actions
    READ_FILE = "read"
    WRITE_FILE = "write"
    EDIT_FILE = "edit"
    BASH = "bash"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    # Delegation
    SUBAGENT_SPAWN = "subagent_spawn"
    SUBAGENT_COMPLETE = "subagent_complete"

    # Context management
    COMPACT = "compact"
    CONTEXT_USAGE = "context_usage"

    # Outcomes
    TEST_RESULT = "test_result"
    COMMIT = "commit"
    ERROR = "error"

    # Permission
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_DECISION = "permission_decision"

    # Usage
    USAGE = "usage"
    FINAL = "final"


class TaskArchetype(str, Enum):
    """Task type for tailored scoring."""

    RESEARCH = "research"
    DEBUG = "debug"
    OPS = "ops"
    FEATURE = "feature"
    EDIT = "edit"
    UNKNOWN = "unknown"


class AgentHost(str, Enum):
    """Supported agent hosts."""

    ZERO = "zero"
    HERMES = "hermes"
    OPENCODE = "opencode"
    CLAUDE_CODE = "claude-code"
    UNKNOWN = "unknown"


@dataclass
class TraceEvent:
    """Canonical telemetry event.

    Every adapter normalizes its host-specific events into this shape.
    """

    type: EventType
    timestamp: float
    agent_id: str
    host: str
    run_id: str
    session_id: str

    # Type-specific payload
    file_path: str | None = None
    command: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_output: str | None = None
    output_size: int | None = None
    input_size: int | None = None

    # Delegation
    subagent_id: str | None = None
    subagent_role: str | None = None
    subagent_prompt: str | None = None

    # Context
    context_tokens: int | None = None
    cache_tokens: int | None = None
    compaction: bool = False

    # Test/outcome
    test_passed: bool | None = None
    test_count: int | None = None
    test_failed: int | None = None
    commit_hash: str | None = None

    # Permission
    permission_action: str | None = None
    permission_granted: bool | None = None

    # Usage
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    # Error
    error_code: str | None = None
    error_message: str | None = None
    recoverable: bool | None = None

    # Arbitrary extra
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, Enum) else self.type
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TraceEvent:
        d = dict(d)
        d["type"] = (
            EventType(d["type"]) if isinstance(d.get("type"), str) else d.get("type")
        )
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RunSummary:
    """Aggregated summary of a single run/session."""

    run_id: str
    session_id: str
    host: str
    agent_id: str
    start_time: float
    end_time: float | None = None

    # Counts
    read_count: int = 0
    write_count: int = 0
    edit_count: int = 0
    bash_count: int = 0
    tool_call_count: int = 0
    subagent_count: int = 0
    error_count: int = 0
    compact_count: int = 0
    test_count: int = 0
    test_failed: int = 0
    commit_count: int = 0

    # Token usage
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    peak_context_tokens: int = 0
    cache_hit_tokens: int = 0

    # Files
    files_read: set[str] = field(default_factory=set)
    files_written: set[str] = field(default_factory=set)
    commands_run: list[str] = field(default_factory=list)

    # Delegation
    subagent_roles: list[str] = field(default_factory=list)

    # Project
    project_key: str = ""

    # Archetype
    archetype: TaskArchetype = TaskArchetype.UNKNOWN

    def duration(self) -> float:
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["files_read"] = list(d["files_read"])
        d["files_written"] = list(d["files_written"])
        d["archetype"] = self.archetype.value
        return d


@dataclass
class EfficiencyReport:
    """Context-efficiency report for a run."""

    run_id: str
    archetype: TaskArchetype

    # 11 metrics (0.0–1.0, higher = better efficiency)
    context_pressure: float = 0.0
    cache_hit_ratio: float = 0.0
    redundant_reads: float = 1.0
    read_amplification: float = 1.0
    large_injections: float = 1.0
    retry_waste: float = 1.0
    yield_density: float = 1.0
    tool_overhead: float = 1.0
    edit_churn: float = 1.0
    large_file_reads: float = 1.0
    unused_reads: float = 1.0

    # Composite
    overall_score: float = 0.0

    # Reclaimable tokens by metric
    reclaimable_tokens: dict[str, int] = field(default_factory=dict)

    # Offenders (file/command basenames)
    offenders: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["archetype"] = self.archetype.value
        return d


@dataclass
class EffectivenessReport:
    """Outcome scoring — did the task actually land?"""

    run_id: str
    score: float = 0.0
    confidence: str = "low"  # low, medium, high
    label: str = "unclear"

    # Signals
    has_edits: bool = False
    has_tests_passed: bool = False
    has_commits: bool = False
    has_errors: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Event type helpers ─────────────────────────────────────────────────────

ACTION_EVENT_TYPES = {
    EventType.READ_FILE,
    EventType.WRITE_FILE,
    EventType.EDIT_FILE,
    EventType.BASH,
    EventType.TOOL_CALL,
    EventType.SUBAGENT_SPAWN,
}

OUTPUT_EVENT_TYPES = {
    EventType.TOOL_RESULT,
    EventType.SUBAGENT_COMPLETE,
    EventType.ERROR,
}

SIDE_EFFECT_EVENTS = {
    EventType.PERMISSION_REQUEST,
    EventType.PERMISSION_DECISION,
    EventType.USAGE,
    EventType.CONTEXT_USAGE,
    EventType.COMPACT,
}
