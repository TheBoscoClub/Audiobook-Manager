"""Verify upgrade.sh never syncs development artifacts into an installation.

``library/web-v2/`` is the reverse proxy's document root. Anything rsync'd
into it is a candidate for retrieval over HTTPS, so a dev artifact that rides
along in the sync is a disclosure, not an untidiness.

These tests source ``_NEVER_SHIP_EXCLUDES`` out of upgrade.sh and drive a real
rsync with it, rather than grepping for exclude strings — the previous defect
was not a missing string but a pattern (`--exclude='.claude'`) that matched a
directory while every `.claude-*` FILE went through it, and only an executed
rsync can tell those two apart.
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPGRADE_SH = PROJECT_ROOT / "upgrade.sh"

TIMEOUT = 30

# (relative path, is_directory) — artifacts that must never reach a target
FORBIDDEN_ARTIFACTS = [
    (".claude-session-ring.jsonl", False),
    (".claude-checkpoint-notes.md", False),
    (".claude-task-state.md", False),
    (".claude/settings.json", True),
    ("library/web-v2/.claude-session-ring.jsonl", False),
    ("library/web-v2/css/.claude-session-ring.jsonl", False),
    ("SESSION_RECORD_2026-01-01.md", False),
    ("library/SESSION_RECORD_2026-01-01.md", False),
    (".snapshots/snap-x/marker", True),
    (".btrbk-snapshots/marker", True),
    (".test-sandbox/marker", True),
    (".staged-release", False),
    ("library/__pycache__/x.cpython-314.pyc", True),
    ("test-results.json", False),
    # Editor/backup litter (Audiobook-Manager-qlr): a gitignored
    # auth.py.backup was shipped to a target because rsync copies the working
    # tree, not the git index.
    ("library/backend/api_modular/auth.py.backup", False),
    ("caddy/audiobooks.conf.bak", False),
    ("library/web-v2/js/api.js.orig", False),
    ("lib/audiobook-config.sh.rej", False),
    ("library/backend/api_server.py~", False),
    ("library/backend/.api_server.py.swp", False),
]

# Files that MUST survive the sync — an over-broad exclude is also a defect
REQUIRED_ARTIFACTS = [
    "library/web-v2/shell.html",
    "library/web-v2/css/deco.css",
    "library/web-v2/js/api.js",
    "library/backend/api_server.py",
    # A real .conf must survive even though *.conf.bak must not.
    "caddy/audiobooks.conf",
    "VERSION",
]


def _build_fake_project(root: Path) -> None:
    """Create a source tree containing both dev artifacts and real assets."""
    for rel, _is_dir in FORBIDDEN_ARTIFACTS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SECRET\n")
    for rel in REQUIRED_ARTIFACTS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("asset\n")


def _never_ship_excludes() -> list[str]:
    """Evaluate the real _NEVER_SHIP_EXCLUDES assignment from upgrade.sh.

    upgrade.sh cannot be sourced (it runs an upgrade), so the array literal is
    sliced out of the source and evaluated on its own. The values still come
    from the shipped script — nothing is duplicated into this test.
    """
    text = UPGRADE_SH.read_text()
    start = text.index("_NEVER_SHIP_EXCLUDES=(")
    end = text.index("\n)", start) + 2
    script = text[start:end] + '\nprintf "%s\\n" "${_NEVER_SHIP_EXCLUDES[@]}"'
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=TIMEOUT, check=False
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line.startswith("--exclude=")]


@pytest.fixture(scope="module")
def excludes() -> list[str]:
    args = _never_ship_excludes()
    assert args, "upgrade.sh must define _NEVER_SHIP_EXCLUDES"
    return args


@pytest.fixture
def synced(tmp_path, excludes):
    """rsync a fake project through the excludes; return the destination."""
    source = tmp_path / "project"
    dest = tmp_path / "installed"
    source.mkdir()
    dest.mkdir()
    _build_fake_project(source)
    result = subprocess.run(
        ["rsync", "-a", "--delete", *excludes, f"{source}/", f"{dest}/"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return dest


class TestNeverShipExcludes:
    @pytest.mark.parametrize("rel,_is_dir", FORBIDDEN_ARTIFACTS)
    def test_dev_artifact_is_not_synced(self, synced, rel, _is_dir):
        assert not (synced / rel).exists(), f"{rel} reached the installation"

    @pytest.mark.parametrize("rel", REQUIRED_ARTIFACTS)
    def test_real_asset_survives(self, synced, rel):
        assert (synced / rel).is_file(), f"{rel} was wrongly excluded"

    def test_dot_claude_glob_not_just_directory(self, excludes):
        """`.claude` alone matched the directory; the FILES need the glob."""
        assert "--exclude=.claude*" in excludes, (
            "the exclude must be a glob — '.claude' matches only the directory, "
            "and '.claude-session-ring.jsonl' is what leaked into web-v2/"
        )


class TestExcludesAreWiredIntoEveryRsync:
    """Every rsync in upgrade.sh must expand the canonical list.

    The library sync — the one that reaches the document root — previously
    carried its own ad-hoc, much shorter exclude set.
    """

    def test_no_rsync_without_the_canonical_excludes(self):
        text = UPGRADE_SH.read_text()
        assert text.count('"${_NEVER_SHIP_EXCLUDES[@]}"') >= 3, (
            "remote sync, library sync and converter sync must all expand _NEVER_SHIP_EXCLUDES"
        )

    def test_library_sync_uses_canonical_excludes(self):
        text = UPGRADE_SH.read_text()
        marker = "Upgrading library components"
        start = text.index(marker)
        block = text[start : start + 1500]
        assert '"${_NEVER_SHIP_EXCLUDES[@]}"' in block, (
            "the library/ rsync writes into the proxy document root and must "
            "expand the canonical exclude list"
        )
