"""Causal timeline builder — compact, redacted action sequence for suggest models.

Port of Agent-Blackbox's buildCausalTimeline (MIT, Taewoo Park).
"""

from __future__ import annotations

from .core import TraceEvent, EventType


class CausalTimeline:
    """Builds a compact, redacted action trace from a run's events.

    Used by the suggest engine to give the model causal context
    (e.g. "a re-read right after compact is legitimate, not waste").
    """

    def build(self, events: list[TraceEvent]) -> list[str]:
        """Return a list of compact action tokens with redacted targets.

        Tokens are space-separated action+target pairs, e.g.:
            "r main.py"
            "e auth.py"
            "b git commit"
            "c"  (compact)
            "w README.md"
        """
        timeline: list[str] = []
        read_set: set[str] = set()

        for ev in events:
            token = self._event_to_token(ev, read_set)
            if token:
                timeline.append(token)

        return timeline

    def _event_to_token(self, ev: TraceEvent, read_set: set[str]) -> str | None:
        basename = self._safe_basename(ev.file_path) if ev.file_path else None
        cmd_verb = self._command_verb(ev.command) if ev.command else None

        if ev.type == EventType.READ_FILE and basename:
            if basename in read_set:
                read_set.add(basename)
                return f"rr {basename}"  # redundant read
            read_set.add(basename)
            return f"r {basename}"

        if ev.type == EventType.WRITE_FILE and basename:
            return f"w {basename}"

        if ev.type == EventType.EDIT_FILE and basename:
            return f"e {basename}"

        if ev.type == EventType.BASH and cmd_verb:
            return f"b {cmd_verb}"

        if ev.type == EventType.COMPACT:
            read_set.clear()  # compaction resets the read window
            return "c"

        if ev.type in (EventType.SUBAGENT_SPAWN, EventType.SUBAGENT_COMPLETE):
            role = (ev.subagent_role or "agent")[:20]
            tag = "spawn" if ev.type == EventType.SUBAGENT_SPAWN else "done"
            return f"sg/{role}/{tag}"

        if ev.type == EventType.ERROR:
            code = ev.error_code or "err"
            return f"err {code}"

        if ev.type == EventType.TOOL_CALL:
            name = (ev.tool_name or "tool")[:15]
            return f"tc {name}"

        if ev.type == EventType.TOOL_RESULT:
            return None  # skip — captured by the tool_call

        if ev.type == EventType.USAGE:
            return None  # skip — captured elsewhere

        return None

    @staticmethod
    def _safe_basename(path: str) -> str:
        """Extract basename, redacting full path."""
        import os.path

        base = os.path.basename(path.rstrip("/\\"))
        return base if base else "?"

    @staticmethod
    def _command_verb(cmd: str) -> str:
        """Extract first word of a command, redacting args."""
        cmd = cmd.strip()
        if not cmd:
            return "?"
        verb = cmd.split()[0]
        # Redact path-like verbs
        verb = verb.replace("../", "").replace("./", "")
        if verb in ("npx", "npm", "pip", "cargo", "go", "make", "bun", "yarn"):
            return verb
        return verb[:15]

    def to_prompt_block(self, events: list[TraceEvent]) -> str:
        """Format as a compact string for LLM suggest prompts."""
        tokens = self.build(events)
        if not tokens:
            return ""
        return "CAUSAL TIMELINE:\n" + " ".join(tokens[:200])
