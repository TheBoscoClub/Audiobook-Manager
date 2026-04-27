"""Tests for uninstall.sh --keep-data user-state preservation.

These exercise the bash `stage_preserved_state` / `restore_preserved_state`
helpers end-to-end by running uninstall.sh in --user mode against a scratch
$HOME. User mode is chosen because:

  * It needs no sudo and mutates nothing outside $HOME.
  * It exercises the same staging helpers the system path uses.
  * systemctl --user read-only calls are safe no-ops on empty fake homes.

The bug this guards against (v8.1.0.1): `--keep-data` used to only preserve
/srv/audiobooks/{Library,Sources,Supplements} and happily wiped
/var/lib/audiobooks — losing the SQLite DB, auth.db, covers cache, and the
user's audiobooks.conf. The fix stages those items before the wipe and
restores them after. These tests fail loudly if any of those items goes
missing again.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNINSTALL = PROJECT_ROOT / "uninstall.sh"


def _run_uninstall(fake_home: Path, *extra_args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Scrub any AUDIOBOOKS_* vars that may have been set during test-suite
    # runs; they would leak into uninstall.sh and cause it to touch real
    # host paths. User-mode uninstall must be hermetic under $HOME.
    for key in list(env):
        if key.startswith("AUDIOBOOKS_"):
            del env[key]
    env["HOME"] = str(fake_home)
    return subprocess.run(
        ["bash", str(UNINSTALL), "--user", "--force", *extra_args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _populate_fake_user_install(home: Path) -> dict[str, Path]:
    """Create the files uninstall.sh should preserve under --keep-data."""
    cfg = home / ".config" / "audiobooks"
    state = home / ".local" / "var" / "lib" / "audiobooks"
    applib = home / ".local" / "lib" / "audiobooks"

    for d in (cfg, state / "db", state / "covers", applib):
        d.mkdir(parents=True, exist_ok=True)

    paths = {
        "db": state / "db" / "audiobooks.db",
        "db_wal": state / "db" / "audiobooks.db-wal",
        "auth_db": state / "auth.db",
        "auth_key": cfg / "auth.key",
        "cover": state / "covers" / "asin123.jpg",
        "conf": cfg / "audiobooks.conf",
        "applib_marker": applib / "marker",
    }
    paths["db"].write_bytes(b"SQLITE_FAKE_DB")
    paths["db_wal"].write_bytes(b"WAL")
    paths["auth_db"].write_bytes(b"AUTH_FAKE")
    paths["auth_key"].write_text("deadbeef" * 8)
    paths["auth_key"].chmod(0o600)
    paths["cover"].write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    paths["conf"].write_text('CUSTOM_KEY="mine"\nAUDIOBOOKS_PARALLEL_JOBS="4"\n')
    paths["applib_marker"].write_text("app installed")

    return paths


@pytest.fixture()
def fake_home(tmp_path: Path):  # Generator[Path, None, None]
    home = tmp_path / "home"
    home.mkdir()
    yield home
    # tmp_path cleans itself up; nothing to restore


def test_keep_data_preserves_db_auth_covers_config(fake_home: Path) -> None:
    paths = _populate_fake_user_install(fake_home)
    original_contents = {k: p.read_bytes() for k, p in paths.items() if p.is_file()}

    result = _run_uninstall(fake_home, "--keep-data")
    assert result.returncode == 0, (
        f"uninstall.sh exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # The app directory SHOULD have been wiped
    assert not paths[
        "applib_marker"
    ].exists(), "applib marker should have been removed (it's installed code, not state)"

    # Every preserved item must still exist with the same content
    missing = [k for k, p in paths.items() if k != "applib_marker" and not p.exists()]
    assert not missing, f"--keep-data lost these preserved files: {missing}"

    for key, expected in original_contents.items():
        if key == "applib_marker":
            continue
        actual = paths[key].read_bytes()
        assert actual == expected, f"content mismatch for {key}: {paths[key]}"

    # auth.key must remain 0600 after restore (restore_preserved_state chmods it)
    mode = paths["auth_key"].stat().st_mode & 0o777
    assert mode == 0o600, f"auth.key mode drifted to {oct(mode)}"


def test_delete_data_wipes_state_dir(fake_home: Path) -> None:
    paths = _populate_fake_user_install(fake_home)

    result = _run_uninstall(fake_home, "--delete-data")
    assert result.returncode == 0, (
        f"uninstall.sh exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # --delete-data should NOT preserve any of the staged items
    should_be_gone = ("db", "db_wal", "auth_db", "auth_key", "cover", "conf")
    survivors = [k for k in should_be_gone if paths[k].exists()]
    assert not survivors, f"--delete-data should have wiped these, but they survived: {survivors}"


def test_keep_data_restores_even_when_some_items_missing(fake_home: Path) -> None:
    """Preservation must tolerate absent items (e.g. first-install with no auth.db yet)."""
    paths = _populate_fake_user_install(fake_home)

    # Simulate a user who never enabled auth: no auth.db, no auth.key
    paths["auth_db"].unlink()
    paths["auth_key"].unlink()

    result = _run_uninstall(fake_home, "--keep-data")
    assert result.returncode == 0, result.stderr

    # DB and config must still come back
    assert paths["db"].exists(), "db was lost when auth files were absent"
    assert paths["conf"].exists(), "config was lost when auth files were absent"
    assert paths["cover"].exists(), "covers were lost when auth files were absent"
    # Absent items must remain absent (don't fabricate)
    assert not paths["auth_db"].exists()
    assert not paths["auth_key"].exists()


def test_uninstall_script_is_executable() -> None:
    """Guard against accidental chmod -x on uninstall.sh."""
    assert UNINSTALL.exists(), f"{UNINSTALL} missing"
    assert os.access(UNINSTALL, os.X_OK), f"{UNINSTALL} not executable"


@pytest.fixture(autouse=True)
def _skip_if_bash_missing() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
