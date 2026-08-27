"""Verify upgrade.sh purges dev artifacts a prior sync leaked onto a target.

Regression guard for Audiobook-Manager-emq: rsync ``--delete`` does NOT remove
receiver files that match an ``--exclude`` pattern, so once a
``.claude-session-ring.jsonl`` landed in ``$target/library/web-v2/`` the very
exclude that stops new leaks froze the old copy in place — invisible to every
future upgrade. ``purge_deployed_dev_artifacts`` is the targeted cleanup that
removes ONLY the dev-artifact set from the served tree while leaving
``venv``/``certs``/``db``/``testdata``/``data`` untouched (a blanket
``rsync --delete-excluded`` would wrongly wipe those).

The real function and its canonical pattern list are sliced out of upgrade.sh
and executed against a seeded tree, rather than grepping for strings — only an
executed purge can prove the prune boundary actually holds.
"""

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPGRADE_SH = PROJECT_ROOT / "upgrade.sh"

pytestmark = pytest.mark.skipif(
    not UPGRADE_SH.is_file(), reason="upgrade.sh not present (deployed installation)"
)

TIMEOUT = 30

# (relative path under library/, is_dir) — must be gone after the purge
LEAKED_ARTIFACTS = [
    ("web-v2/.claude-session-ring.jsonl", False),
    ("web-v2/css/.claude-session-ring.jsonl", False),
    ("backend/api_modular/auth.py.backup", False),
    ("SESSION_RECORD_2026-01-01.md", False),
    ("x.py.bak", False),
    ("y.orig", False),
    ("stray.pyc", False),
    ("__pycache__/z.cpython-314.pyc", True),
]

# (relative path, is_dir) — must SURVIVE (excluded-and-must-persist, or real)
PROTECTED_ARTIFACTS = [
    ("venv/lib/site.py", False),
    ("venv/.claude-session-ring.jsonl", False),  # inside a pruned dir → kept
    ("certs/server.pem", False),
    ("db/audiobooks.db", False),
    ("testdata/sample.m4b", False),
    ("data/state.json", False),
    ("web-v2/shell.html", False),
    ("backend/api_server.py", False),
]


def _purge_function() -> str:
    text = UPGRADE_SH.read_text()
    start = text.index("_PURGE_DEV_ARTIFACT_NAMES=(")
    end = text.index("# Core Upgrade", start)
    return text[start:end]


def _seed(library: Path) -> None:
    for rel, is_dir in LEAKED_ARTIFACTS + PROTECTED_ARTIFACTS:
        path = library / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
        assert not is_dir or path.exists()


def _run_purge(library: Path, dry_run: str) -> subprocess.CompletedProcess:
    script = (
        "set -uo pipefail\n"
        "RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''\n"
        f'DRY_RUN="{dry_run}"\n'
        + _purge_function()
        + f'purge_deployed_dev_artifacts "{library}" ""\n'
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=TIMEOUT, check=False
    )


class TestPurgeDeployedDevArtifacts:
    @pytest.fixture
    def purged(self, tmp_path):
        library = tmp_path / "library"
        _seed(library)
        result = _run_purge(library, "false")
        assert result.returncode == 0, result.stdout + result.stderr
        return library

    @pytest.mark.parametrize("rel,_is_dir", LEAKED_ARTIFACTS)
    def test_leaked_artifact_purged(self, purged, rel, _is_dir):
        assert not (purged / rel).exists(), f"{rel} survived the purge"

    @pytest.mark.parametrize("rel,_is_dir", PROTECTED_ARTIFACTS)
    def test_protected_artifact_survives(self, purged, rel, _is_dir):
        assert (purged / rel).is_file(), f"{rel} was wrongly purged"

    def test_dry_run_deletes_nothing(self, tmp_path):
        library = tmp_path / "library"
        _seed(library)
        result = _run_purge(library, "true")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "DRY-RUN" in result.stdout
        for rel, _is_dir in LEAKED_ARTIFACTS:
            assert (library / rel).exists(), f"dry-run wrongly removed {rel}"


class TestPurgeIsWiredIntoUpgrade:
    def test_purge_called_after_library_sync(self):
        text = UPGRADE_SH.read_text()
        assert "purge_deployed_dev_artifacts()" in text, "purge function missing"
        marker = "Upgrading library components"
        block = text[text.index(marker) : text.index("Upgrade converter")]
        assert "purge_deployed_dev_artifacts " in block, (
            "the purge must run right after the library rsync so it scrubs the served tree"
        )
