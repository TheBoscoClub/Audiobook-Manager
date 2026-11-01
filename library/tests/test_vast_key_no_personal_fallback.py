"""gpu-node.sh / gpu-budget-guard.sh: no personal secret-injector fallback (bd Audiobook-Manager-6lv).

Without VAST_API_KEY or VAST_API_KEY_FILE both tools used to re-exec
themselves under a secret-injection wrapper found at a path inside the
invoking user's home. A code default never encodes one operator's tooling:
the tools now fail with a generic message and never look in the home
directory. HOME is pointed at a temp dir holding a decoy wrapper at the old
path; the decoy only touches a marker (it never calls its own name or the
script back), so a run that executes it is caught without looping.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

REPO = Path(__file__).resolve().parents[2]
CASES = {
    "gpu-node.sh": ["status"],
    "gpu-budget-guard.sh": ["--cap", "1", "--check"],
}


@pytest.mark.parametrize("script", sorted(CASES))
def test_no_key_fails_generically_and_never_touches_home(tmp_path: Path, script: str) -> None:
    home = tmp_path / "home"
    decoy = home / ".claude" / "bin" / "with-secret"
    decoy.parent.mkdir(parents=True)
    marker = tmp_path / "decoy-ran"
    decoy.write_text(f'#!/bin/bash\n/usr/bin/touch "{marker}"\nexit 97\n')
    decoy.chmod(0o755)
    # A PATH entry holding a same-named decoy too: the tool must not search PATH
    # for an injector either.
    path_decoy_dir = tmp_path / "pathbin"
    path_decoy_dir.mkdir()
    (path_decoy_dir / "with-secret").write_text(decoy.read_text())
    (path_decoy_dir / "with-secret").chmod(0o755)

    env = {
        "PATH": f"{path_decoy_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "GUARD_LOG": str(tmp_path / "guard.log"),
    }
    proc = subprocess.run(
        ["bash", str(REPO / "scripts" / script), *CASES[script]],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert not marker.exists(), "the tool executed a secret-injector it found on its own"
    assert "VAST_API_KEY" in proc.stderr and "VAST_API_KEY_FILE" in proc.stderr, proc.stderr
    assert ".claude" not in proc.stderr + proc.stdout, proc.stderr
    assert "with-secret" not in proc.stderr + proc.stdout, proc.stderr


@pytest.mark.parametrize("script", sorted(CASES))
def test_source_names_no_home_tooling_path(script: str) -> None:
    src = (REPO / "scripts" / script).read_text()
    assert "~/.claude/bin" not in src
    assert "${HOME}/.claude" not in src
    assert "with-secret" not in src


def test_key_from_environment_still_accepted(tmp_path: Path) -> None:
    """The supported path is unchanged: VAST_API_KEY in the environment."""
    proc = subprocess.run(
        ["bash", str(REPO / "scripts" / "gpu-node.sh"), "onstart"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "VAST_API_KEY": "dummy"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
