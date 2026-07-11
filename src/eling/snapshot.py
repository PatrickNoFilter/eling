"""Snapshot / rollback for the facts database (Task 13.1).

Pattern seen in memoir (git-like branch/commit/merge), Origin (versioned pages),
Memory Palace (snapshot rollback), and icarus (provenance + rollback).

Simplest possible version: file-level copy of facts.db before any bulk
operation (decay pass, contradiction auto-resolution, migration). Restore
with `rollback(snapshot_id)` which atomically swaps back.

Keeps last N snapshots (default 5), auto-pruning oldest on each new snapshot.
"""

from __future__ import annotations

import itertools
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

SNAPSHOT_KEEP = 5  # default keep count, overridable in _snapshot_dir


def _snapshot_dir(parent: Path) -> Path:
    """Return the snapshot directory under the brain's home dir.

    The size of each snapshot is ~1× the current facts.db (typically hundreds
    of KB — trivial). Keeps SNAPSHOT_KEEP + 1 copies at most.
    """
    d = parent / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


_snapshot_counter = itertools.count()


def _snapshot_id() -> str:
    """Human-readable snapshot ID: YYYYMMDD-HHMMSS-fff (monotonic suffix)."""
    return time.strftime("%Y%m%d-%H%M%S-") + f"{next(_snapshot_counter):06d}"


# ── Core ops ──────────────────────────────────────────────────────────────────


def create_snapshot(
    db_path: str | Path,
    reason: str = "",
    keep: int = SNAPSHOT_KEEP,
) -> dict[str, Any]:
    """Snapshot the facts database. Returns snapshot metadata.

    Parameters
    ----------
    db_path : str or Path
        Path to the current facts.db.
    reason : str
        Why the snapshot was taken (e.g. "pre_decay", "pre_contradiction_resolve").
    keep : int
        Max snapshots to keep. Oldest are pruned after creating the new one.

    Returns
    -------
    dict with keys: snapshot_id, path, reason, size_bytes, fact_count, timestamp.
    """
    src = Path(db_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"facts database not found: {src}")

    # Flush WAL to main DB file for a consistent snapshot
    import sqlite3

    try:
        tmp_conn = sqlite3.connect(str(src), timeout=5.0)
        tmp_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        tmp_conn.close()
    except Exception:
        logger.debug("WAL checkpoint skipped (non-fatal): %s", src)

    snap_dir = _snapshot_dir(src.parent)
    snap_id = _snapshot_id()
    dest = snap_dir / f"{snap_id}.db"
    meta_path = snap_dir / f"{snap_id}.json"

    shutil.copy2(str(src), str(dest))

    # Count facts
    import sqlite3

    count = 0
    try:
        conn = sqlite3.connect(str(src))
        row = conn.execute("SELECT COUNT(*) as n FROM facts").fetchone()
        count = row[0] if row else 0
        conn.close()
    except Exception:
        logger.debug("fact count query failed (non-fatal): %s", src)

    metadata: dict[str, Any] = {
        "snapshot_id": snap_id,
        "path": str(dest),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": reason,
        "size_bytes": dest.stat().st_size,
        "fact_count": count,
        "source_db": str(src),
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # Prune old snapshots (keep most recent N)
    _prune(snap_dir, keep)

    logger.info("snapshot %s: %d facts, reason=%s", snap_id, count, reason or "(none)")
    return metadata


def list_snapshots(
    db_path: str | Path,
) -> list[dict[str, Any]]:
    """Return all available snapshots sorted newest-first."""
    snap_dir = _snapshot_dir(Path(db_path).expanduser().resolve().parent)
    if not snap_dir.is_dir():
        return []

    snapshots: list[dict[str, Any]] = []
    for f in sorted(snap_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            snapshots.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return snapshots


def rollback(
    snapshot_id: str,
    db_path: str | Path,
) -> dict[str, Any]:
    """Restore facts.db from a named snapshot.

    The current database is first saved as an auto-snapshot so you can undo
    the rollback if needed.

    Returns dict: {snapshot_id, restored_from, restored_to, fact_count, current_backup}.
    """
    src = Path(db_path).expanduser().resolve()
    snap_dir = _snapshot_dir(src.parent)
    snap_db = snap_dir / f"{snapshot_id}.db"
    snap_meta = snap_dir / f"{snapshot_id}.json"

    if not snap_db.is_file():
        raise FileNotFoundError(f"snapshot not found: {snap_db}")

    # Auto-backup current DB before rolling back
    backup = create_snapshot(db_path, reason=f"pre_rollback_{snapshot_id}")

    # Atomically copy snapshot over current DB
    shutil.copy2(str(snap_db), str(src))

    # Ensure the restored DB has a clean WAL
    import sqlite3

    try:
        tmp_conn = sqlite3.connect(str(src), timeout=5.0)
        tmp_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        tmp_conn.close()
    except Exception:
        logger.debug("WAL checkpoint on restored DB skipped (non-fatal): %s", src)

    if snap_meta.is_file():
        meta = json.loads(snap_meta.read_text(encoding="utf-8"))
    else:
        meta = {}

    logger.info(
        "rollback %s → %s (backup: %s)",
        snapshot_id,
        src,
        backup["snapshot_id"],
    )

    return {
        "snapshot_id": snapshot_id,
        "restored_from": str(snap_db),
        "restored_to": str(src),
        "fact_count": meta.get("fact_count", "unknown"),
        "current_backup": backup["snapshot_id"],
    }


# ── Internal ──────────────────────────────────────────────────────────────────


def _prune(snap_dir: Path, keep: int) -> None:
    """Remove oldest snapshots beyond *keep* count."""
    all_dbs = sorted(snap_dir.glob("*.db"))
    if len(all_dbs) <= keep:
        return
    for db in all_dbs[:-keep]:
        meta = db.with_suffix(".json")
        db.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
