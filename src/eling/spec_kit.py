"""Spec-kit verifier — check code implementation against spec/plan/tasks.

Reads spec-kit artifacts (.specify/memory/constitution.md,
specs/<feature>/spec.md, plan.md, tasks.md) and reports which
requirements are covered by the current implementation.

Usage:
    from eling.spec_kit import SpecKitVerifier
    v = SpecKitVerifier("/path/to/project")
    report = v.verify(changed_files=["src/main.py"])
    print(report["nudge"])
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Artifact paths (spec-kit convention)
# ---------------------------------------------------------------------------

CONSTITUTION_PATH = ".specify/memory/constitution.md"
SPEC_GLOB = "specs/*/spec.md"
PLAN_GLOB = "specs/*/plan.md"
TASKS_GLOB = "specs/*/tasks.md"

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SpecRequirement:
    """A single requirement extracted from a spec.md."""

    def __init__(
        self,
        text: str,
        section: str = "",
        source_file: str = "",
        line: int = 0,
    ):
        self.text = text.strip()
        self.section = section
        self.source_file = source_file
        self.line = line
        self.covered = False
        self.covered_by: list[str] = []

    def to_dict(self) -> dict:
        return {
            "text": self.text[:120],
            "section": self.section,
            "source_file": str(self.source_file),
            "line": self.line,
            "covered": self.covered,
            "covered_by": self.covered_by[:5],
        }


class SpecArtifact:
    """Representation of all spec-kit artifacts for a project."""

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.constitution: str = ""
        self.requirements: list[SpecRequirement] = []
        self.plan_sections: list[str] = []
        self.tasks: list[dict] = []
        self.feature_dirs: list[str] = []
        self._loaded = False

    @property
    def detected(self) -> bool:
        return self._loaded and bool(self.requirements)

    @property
    def constitution_present(self) -> bool:
        return bool(self.constitution)

    @property
    def covered_count(self) -> int:
        return sum(1 for r in self.requirements if r.covered)

    @property
    def uncovered_count(self) -> int:
        return sum(1 for r in self.requirements if not r.covered)

    @property
    def total_count(self) -> int:
        return len(self.requirements)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _find_spec_dirs(project_path: Path) -> list[Path]:
    """Find all spec feature directories under ``specs/``."""
    specs_root = project_path / "specs"
    if not specs_root.is_dir():
        return []
    return sorted([d for d in specs_root.iterdir() if d.is_dir()])


def _parse_markdown_sections(text: str) -> list[tuple[str, str, int]]:
    """Extract headings + their body text from markdown.

    Returns list of (heading, body_text, start_line).
    """
    sections: list[tuple[str, str, int]] = []
    current_heading = "preamble"
    current_body: list[str] = []
    start_line = 0

    for i, line in enumerate(text.split("\n"), 1):
        m = re.match(r"^#{1,4}\s+(.+)$", line)
        if m:
            if current_body:
                sections.append(
                    (current_heading, "\n".join(current_body).strip(), start_line)
                )
            current_heading = m.group(1).strip()
            current_body = []
            start_line = i
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_heading, "\n".join(current_body).strip(), start_line))

    return sections


def _extract_requirements(text: str, source_file: str) -> list[SpecRequirement]:
    """Extract requirements from spec markdown.

    Looks for:
    - Bullet/list items under "Requirements", "User Stories", "Acceptance Criteria"
    - Numbered checklist items
    - **bold** requirement statements
    """
    reqs: list[SpecRequirement] = []
    current_section = ""
    lines = text.split("\n")

    for i, line in enumerate(lines, 1):
        # Track section headings
        hm = re.match(r"^#{1,4}\s+(.+)$", line)
        if hm:
            current_section = hm.group(1).strip()
            continue

        stripped = line.strip()

        # Bullet/list items that look like requirements
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            content = bullet.group(1).strip()
            if len(content) > 15 and not content.startswith("["):
                reqs.append(
                    SpecRequirement(
                        text=content,
                        section=current_section,
                        source_file=source_file,
                        line=i,
                    )
                )
            continue

        # Numbered items
        numbered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if numbered:
            content = numbered.group(1).strip()
            if len(content) > 15:
                reqs.append(
                    SpecRequirement(
                        text=content,
                        section=current_section,
                        source_file=source_file,
                        line=i,
                    )
                )
            continue

        # Checklist items: - [ ] or * [ ]
        checklist = re.match(r"^[-*+]\s+\[\s*[ xX]?\s*\]\s+(.+)$", stripped)
        if checklist:
            content = checklist.group(1).strip()
            if len(content) > 10:
                reqs.append(
                    SpecRequirement(
                        text=content,
                        section=current_section,
                        source_file=source_file,
                        line=i,
                    )
                )
            continue

    return reqs


def _extract_tasks(text: str, source_file: str) -> list[dict]:
    """Extract task list from tasks.md.

    Returns list of {task, file_refs, section}.
    """
    tasks: list[dict] = []
    current_section = ""
    lines = text.split("\n")

    for i, line in enumerate(lines, 1):
        hm = re.match(r"^#{1,4}\s+(.+)$", line)
        if hm:
            current_section = hm.group(1).strip()
            continue

        stripped = line.strip()

        # Checklist task: - [ ] or * [ ]
        m = re.match(r"^[-*+]\s+\[\s*[ xX]?\s*\]\s+(.+)$", stripped)
        if m:
            content = m.group(1).strip()
            file_refs = re.findall(r"`([^`]+)`", content)
            tasks.append(
                {
                    "task": content[:200],
                    "file_refs": file_refs,
                    "section": current_section,
                    "source_file": str(source_file),
                    "line": i,
                }
            )

    return tasks


def _extract_plan_sections(text: str) -> list[str]:
    """Extract implementation-relevant sections from plan.md."""
    sections = []
    for heading, body, _line in _parse_markdown_sections(text):
        if any(
            kw in heading.lower()
            for kw in [
                "implementation",
                "architecture",
                "component",
                "api",
                "data model",
                "database",
                "frontend",
                "backend",
            ]
        ):
            sections.append(f"## {heading}\n\n{body[:500]}")
    return sections


# ---------------------------------------------------------------------------
# Coverage analysis
# ---------------------------------------------------------------------------


def _compute_coverage(
    requirements: list[SpecRequirement],
    changed_files: list[str],
    all_project_files: list[str],
) -> None:
    """Mark requirements as covered if relevant files reference them.

    Heuristic: a requirement is "covered" when a code file's path
    or content overlaps with terms from the requirement text.
    """
    for req in requirements:
        # Extract significant terms from requirement
        terms = set()
        for word in req.text.lower().split():
            word = word.strip(".,;:!?()[]{}\"'")
            if len(word) > 3 and word not in _STOP_WORDS:
                terms.add(word)

        # Check if any changed file path matches
        req.covered_by = []
        for fpath in all_project_files:
            fp_lower = fpath.lower()
            # Check if any term appears in the file path
            for term in terms:
                if term in fp_lower:
                    req.covered_by.append(fpath)
                    break

        # Check changed files specifically
        for cf in changed_files:
            if any(term in cf.lower() for term in terms):
                if cf not in req.covered_by:
                    req.covered_by.append(cf)

        req.covered = len(req.covered_by) > 0


_STOP_WORDS: frozenset[str] = frozenset(
    {
        "should",
        "would",
        "could",
        "must",
        "shall",
        "will",
        "need",
        "able",
        "used",
        "use",
        "using",
        "user",
        "users",
        "also",
        "well",
        "one",
        "two",
        "new",
        "make",
        "made",
        "support",
        "based",
        "within",
        "without",
        "across",
        "after",
        "before",
        "between",
        "other",
        "each",
        "every",
        "both",
        "first",
        "last",
        "being",
        "done",
        "does",
        "doing",
        "having",
        "have",
        "has",
        "than",
        "then",
        "that",
        "this",
        "these",
        "those",
        "which",
        "what",
        "when",
        "where",
        "their",
        "them",
        "they",
        "your",
        "from",
        "into",
        "over",
        "such",
        "some",
        "more",
        "most",
        "many",
        "much",
        "very",
        "just",
        "about",
        "than",
        "down",
        "back",
        "still",
        "already",
        "always",
        "never",
        "ever",
        "here",
        "there",
        "only",
        "really",
        "way",
        "thing",
        "things",
    }
)


# ---------------------------------------------------------------------------
# Nudge builder
# ---------------------------------------------------------------------------


def build_spec_nudge(artifact: SpecArtifact, changed_files: list[str]) -> str | None:
    """Build a verification nudge referencing spec-kit requirements.

    Returns None if no spec-kit artifacts found or all requirements covered.
    """
    if not artifact.detected:
        return None

    uncovered = [r for r in artifact.requirements if not r.covered]
    if not uncovered:
        return None

    lines: list[str] = [
        "[System: Spec-kit requirements pending verification",
        "",
    ]

    if artifact.constitution:
        lines.append(f"Constitution: {artifact.constitution[:80]}...")

    lines.append(
        f"\nSpec coverage: {artifact.covered_count}/{artifact.total_count} "
        f"requirements covered by code"
    )

    if artifact.tasks:
        lines.append(f"Tasks defined: {len(artifact.tasks)}")

    if uncovered:
        lines.append(f"\nUncovered requirements ({len(uncovered)}):")
        for req in uncovered[:5]:
            section_tag = f"[{req.section}]" if req.section else ""
            lines.append(f"  {section_tag} {req.text[:100]}")
        if len(uncovered) > 5:
            lines.append(f"  ... and {len(uncovered) - 5} more")

    if changed_files:
        lines.append(f"\nRecently changed files ({len(changed_files)}):")
        for cf in changed_files[:5]:
            lines.append(f"  - {cf}")
        if len(changed_files) > 5:
            lines.append(f"  ... and {len(changed_files) - 5} more")

    lines.append(
        "\nReview the spec requirements above and ensure the implementation"
        "\naddresses each one. Run verification (tests/lint/build) and"
        "\nrecord the result with eling_verify.]"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SpecKitVerifier:
    """Verifies code implementation against spec-kit artifacts."""

    def __init__(self, project_path: str | Path | None = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()

    def detect(self) -> bool:
        """Check if the project has spec-kit artifacts."""
        specs_root = self.project_path / "specs"
        return specs_root.is_dir() or (self.project_path / CONSTITUTION_PATH).exists()

    def load(self) -> SpecArtifact:
        """Load all spec-kit artifacts from the project."""
        artifact = SpecArtifact(self.project_path)

        # Constitution
        con_path = self.project_path / CONSTITUTION_PATH
        if con_path.exists():
            artifact.constitution = con_path.read_text(
                encoding="utf-8", errors="replace"
            )[:2000]

        # Feature specs
        spec_dirs = _find_spec_dirs(self.project_path)
        artifact.feature_dirs = [d.name for d in spec_dirs]

        for sdir in spec_dirs:
            # spec.md
            spec_file = sdir / "spec.md"
            if spec_file.exists():
                text = spec_file.read_text(encoding="utf-8", errors="replace")
                reqs = _extract_requirements(text, str(spec_file))
                artifact.requirements.extend(reqs)

            # plan.md
            plan_file = sdir / "plan.md"
            if plan_file.exists():
                text = plan_file.read_text(encoding="utf-8", errors="replace")
                artifact.plan_sections.extend(_extract_plan_sections(text))

            # tasks.md
            tasks_file = sdir / "tasks.md"
            if tasks_file.exists():
                text = tasks_file.read_text(encoding="utf-8", errors="replace")
                artifact.tasks.extend(_extract_tasks(text, str(tasks_file)))

        artifact._loaded = True
        return artifact

    def verify(
        self,
        changed_files: list[str] | None = None,
        all_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run spec-kit verification and return a report.

        Parameters
        ----------
        changed_files:
            Files that were modified in the current session.
        all_files:
            All project files (for coverage analysis). Auto-discovered if omitted.

        Returns
        -------
        dict with: detected, requirements, coverage, nudge, summary
        """
        if not self.detect():
            return {
                "detected": False,
                "summary": "No spec-kit artifacts found (no specs/ directory)",
                "nudge": None,
                "requirements": [],
                "coverage": {"covered": 0, "uncovered": 0, "total": 0},
            }

        artifact = self.load()

        if not artifact.requirements:
            return {
                "detected": True,
                "summary": "Spec-kit detected but no requirements extracted",
                "nudge": None,
                "requirements": [],
                "coverage": {"covered": 0, "uncovered": 0, "total": 0},
            }

        # Discover all project files if not provided
        if all_files is None:
            all_files = [
                str(p.relative_to(self.project_path))
                for p in self.project_path.rglob("*")
                if p.is_file()
                and ".git" not in p.parts
                and "__pycache__" not in p.parts
                and "node_modules" not in p.parts
                and ".specify" not in p.parts
            ]

        _compute_coverage(artifact.requirements, changed_files or [], all_files)

        nudge = build_spec_nudge(artifact, changed_files or [])

        return {
            "detected": True,
            "summary": (
                f"{artifact.covered_count}/{artifact.total_count} requirements "
                f"covered ({artifact.uncovered_count} uncovered)"
            ),
            "nudge": nudge,
            "requirements": [r.to_dict() for r in artifact.requirements],
            "coverage": {
                "covered": artifact.covered_count,
                "uncovered": artifact.uncovered_count,
                "total": artifact.total_count,
            },
            "features": artifact.feature_dirs,
            "tasks": len(artifact.tasks),
            "constitution_present": bool(artifact.constitution),
        }


__all__ = [
    "SpecKitVerifier",
    "SpecArtifact",
    "SpecRequirement",
    "build_spec_nudge",
]
