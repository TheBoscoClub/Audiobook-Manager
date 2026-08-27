"""Every shipped migration script must be executable, with a shebang.

``data-migrations/014_original_publish_year.sh`` shipped as mode 100644 while
its thirteen siblings were 100755. upgrade.sh `source`s these rather than
exec'ing them, so nothing failed — which is precisely why the drift survived a
release: the file carries a `#!/bin/bash` shebang and reads as executable, and
only `git ls-files -s` disagrees. The next migration runner that exec's a
script (or any operator running it by hand) hits "Permission denied" on one
arbitrary file out of fourteen.
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIRS = ("data-migrations", "config-migrations")


def _tracked_modes(directory: str) -> dict[str, str]:
    """Return {path: git mode} for tracked .sh files in a directory."""
    result = subprocess.run(
        ["git", "ls-files", "-s", directory],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    modes = {}
    for line in result.stdout.splitlines():
        meta, _, path = line.partition("\t")
        if path.endswith(".sh"):
            modes[path] = meta.split()[0]
    return modes


@pytest.mark.parametrize("directory", MIGRATION_DIRS)
def test_all_migration_scripts_are_executable_in_git(directory):
    modes = _tracked_modes(directory)
    assert modes, f"no tracked .sh files found in {directory}"
    non_exec = sorted(path for path, mode in modes.items() if mode != "100755")
    assert not non_exec, "migration scripts tracked without the executable bit: " + ", ".join(
        non_exec
    )


@pytest.mark.parametrize("directory", MIGRATION_DIRS)
def test_all_migration_scripts_are_executable_on_disk(directory):
    base = PROJECT_ROOT / directory
    scripts = sorted(base.glob("*.sh"))
    assert scripts, f"no .sh files found in {directory}"
    non_exec = [str(p.relative_to(PROJECT_ROOT)) for p in scripts if not p.stat().st_mode & 0o111]
    assert not non_exec, "migration scripts not executable on disk: " + ", ".join(non_exec)


@pytest.mark.parametrize("directory", MIGRATION_DIRS)
def test_all_migration_scripts_declare_bash(directory):
    """`#!/bin/bash` per the project's shell rule — no sh, no zsh."""
    for script in sorted((PROJECT_ROOT / directory).glob("*.sh")):
        first_line = script.read_text().splitlines()[0]
        assert first_line == "#!/bin/bash", (
            f"{script.relative_to(PROJECT_ROOT)} has shebang {first_line!r}"
        )
