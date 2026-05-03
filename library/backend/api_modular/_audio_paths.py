"""Production-parity path resolution for DB-stored file paths.

The DB carries absolute file paths captured at scan time. The local
install may root its data at a different absolute prefix (e.g., the
operator may set ``AUDIOBOOKS_LIBRARY`` or ``AUDIOBOOKS_SUPPLEMENTS``
to a non-default location). This module rebases stored paths under the
local roots at request time so the same DB rows resolve correctly across
environments without any per-environment data mutation.

Two thin wrappers expose the per-root resolvers used by the API:

- :func:`resolve_local_audio_path` — for ``audiobooks.file_path`` rows,
  rooted under the canonical ``Library/`` segment.
- :func:`resolve_local_supplement_path` — for ``supplements.file_path``
  rows, rooted under the canonical ``Supplements/`` segment.

Both share :func:`_resolve_under_root`, which performs identity-then-rebase
resolution with traversal-escape protection. None of the constants or
defaults below are operator-specific — they are the canonical project
install conventions also used by ``library/config.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

# ─── Canonical project defaults sourced from library.config ────────────────
#
# Single source of truth for path defaults is ``library/config.py``. This
# helper imports from there so the canonical install convention has exactly
# one definition (per the project's no-hardcoded-paths rule). Operators
# override at runtime via ``AUDIOBOOKS_LIBRARY`` / ``AUDIOBOOKS_SUPPLEMENTS``
# in ``/etc/audiobooks/audiobooks.conf``; this module reads the env var at
# request time and only falls back to the config-defined default.
from config import AUDIOBOOKS_LIBRARY as _CFG_LIBRARY
from config import AUDIOBOOKS_SUPPLEMENTS as _CFG_SUPPLEMENTS

DEFAULT_AUDIOBOOKS_LIBRARY = str(_CFG_LIBRARY)
DEFAULT_AUDIOBOOKS_SUPPLEMENTS = str(_CFG_SUPPLEMENTS)

# Canonical project conventions for the directory segment that anchors
# the relative subpath used during rebase.
LIBRARY_SEGMENT = "Library"
SUPPLEMENTS_SEGMENT = "Supplements"


def _resolve_under_root(
    stored_path: str | os.PathLike[str],
    *,
    segment: str,
    env_var: str,
    default_root: str,
) -> Path | None:
    """Resolve ``stored_path`` under ``env_var`` (falling back to ``default_root``).

    Resolution order:
      1. **Identity** — return the stored path unchanged if it exists on disk
         (covers any environment whose root matches the scan root).
      2. **Rebase** — split the stored path at ``segment``, take the relative
         subpath after it, and join under the local root (``env_var`` value
         or ``default_root`` fallback).

    Returns ``None`` if neither candidate exists. Callers should treat
    ``None`` as "file not found on disk" and respond accordingly (HTTP 404).

    Defensive: rejects rebased candidates that escape the local root via
    ``..`` traversal segments. No operator-specific path literals appear
    in this helper or its arguments.
    """
    stored = Path(stored_path)
    if stored.exists():
        return stored

    parts = stored.parts
    if segment not in parts:
        return None

    segment_idx = parts.index(segment)
    relative = Path(*parts[segment_idx + 1 :])

    local_root = Path(os.environ.get(env_var, default_root))
    candidate = (local_root / relative).resolve()

    # Defensive: ensure the resolved candidate is actually under the local
    # root (no traversal escape via ".." segments in the relative subpath).
    try:
        candidate.relative_to(local_root.resolve())
    except ValueError:
        return None

    return candidate if candidate.exists() else None


def resolve_local_audio_path(stored_path: str | os.PathLike[str]) -> Path | None:
    """Resolve a DB-stored audiobook ``file_path`` to an existing local path.

    Anchored at the canonical ``Library/`` segment; rebases under
    ``AUDIOBOOKS_LIBRARY`` (or ``DEFAULT_AUDIOBOOKS_LIBRARY`` when unset).

    See :func:`_resolve_under_root` for the resolution algorithm.
    """
    return _resolve_under_root(
        stored_path,
        segment=LIBRARY_SEGMENT,
        env_var="AUDIOBOOKS_LIBRARY",
        default_root=DEFAULT_AUDIOBOOKS_LIBRARY,
    )


def resolve_local_supplement_path(stored_path: str | os.PathLike[str]) -> Path | None:
    """Resolve a DB-stored supplement ``file_path`` to an existing local path.

    Anchored at the canonical ``Supplements/`` segment; rebases under
    ``AUDIOBOOKS_SUPPLEMENTS`` (or ``DEFAULT_AUDIOBOOKS_SUPPLEMENTS``
    when unset).

    See :func:`_resolve_under_root` for the resolution algorithm.
    """
    return _resolve_under_root(
        stored_path,
        segment=SUPPLEMENTS_SEGMENT,
        env_var="AUDIOBOOKS_SUPPLEMENTS",
        default_root=DEFAULT_AUDIOBOOKS_SUPPLEMENTS,
    )
