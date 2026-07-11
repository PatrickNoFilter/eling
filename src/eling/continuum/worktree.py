"""Continuum Layer 6 — git worktree helpers.

Dispatch a fresh agent into an isolated git worktree so parallel agents never
clobber each other. Pure ``subprocess`` (no GitPython dependency) so it runs
natively on Termux / PRoot where a minimal Python is all we have.

Commands use the repo's own ``git``; on PRoot/Android we rely on whatever git
the environment provides. No special casing needed — git works the same.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 120


def _run_git(cwd: str, *args: str) -> str:
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git not found on PATH") from exc
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def is_git_repo(project: str) -> bool:
    p = Path(project).expanduser().resolve()
    if not p.exists():
        return False
    try:
        _run_git(str(p), "rev-parse", "--is-inside-work-tree")
        return True
    except Exception:
        return False


def current_branch(project: str) -> str:
    return _run_git(
        str(Path(project).expanduser().resolve()), "rev-parse", "--abbrev-ref", "HEAD"
    )


def dispatch_worktree(
    project: str,
    branch: str,
    base: Optional[str] = None,
) -> str:
    """Create and check out an isolated worktree on ``branch``.

    Returns the absolute path to the new worktree. The base branch defaults to
    the project's current branch. Mirrors Continuum's "fresh agent in isolated
    worktree" dispatch.
    """
    p = Path(project).expanduser().resolve()
    if not is_git_repo(str(p)):
        raise RuntimeError(f"not a git repo: {p}")
    base = base or current_branch(str(p))
    wt_dir = p.parent / f"{p.name}.worktrees" / branch
    # idempotent: remove a stale worktree record if the dir is gone
    try:
        _run_git(str(p), "worktree", "add", "--force", "--detach", str(wt_dir), base)
    except RuntimeError as exc:
        # Fall back to branch-based add if detached failed
        try:
            _run_git(str(p), "worktree", "add", "--force", str(wt_dir), base)
        except RuntimeError:
            raise exc
    # Rename the detached HEAD branch to the requested branch name inside wt
    try:
        _run_git(str(wt_dir), "checkout", "-B", branch)
    except RuntimeError:
        logger.debug("worktree checkout -B failed (non-fatal): %s", branch)
    return str(wt_dir)


def remove_worktree(project: str, worktree_path: str) -> None:
    """Remove a worktree (prunes it). Safe to call when already gone."""
    p = Path(project).expanduser().resolve()
    wt = Path(worktree_path).expanduser().resolve()
    try:
        _run_git(str(p), "worktree", "remove", "--force", str(wt))
    except RuntimeError as exc:
        # Already removed or locked — try prune, then ignore.
        try:
            _run_git(str(p), "worktree", "prune")
        except RuntimeError:
            logger.debug("worktree prune failed (non-fatal): %s", str(wt))
        logger.debug("worktree removal note: %s", exc)


def list_worktrees(project: str) -> list[str]:
    p = Path(project).expanduser().resolve()
    out = _run_git(str(p), "worktree", "list", "--porcelain")
    paths: list[str] = []
    for block in out.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("worktree "):
                paths.append(line[len("worktree ") :].strip())
    return paths
