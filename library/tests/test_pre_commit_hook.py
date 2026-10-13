"""Behavioural tests for scripts/hooks/pre-commit (hardcoded-path guard).

Each test builds a throwaway git repository under tmp_path, stages a file and
runs the hook script against it, exactly as git would. Set
PRE_COMMIT_HOOK_UNDER_TEST to point at another copy of the hook (used to prove
the tests go red against the pre-fix hook).
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = Path(os.environ.get("PRE_COMMIT_HOOK_UNDER_TEST", REPO_ROOT / "scripts/hooks/pre-commit"))


def _run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _stage(repo: Path, relpath: str, content: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=env)
    return tmp_path


@pytest.mark.parametrize(
    ("literal", "variable"),
    [
        ("/srv/audiobooks/Library", "AUDIOBOOKS_DATA"),
        ("/var/lib/audiobooks/db/audiobooks.db", "AUDIOBOOKS_VAR_DIR"),
        ("/run/audiobooks/api.sock", "AUDIOBOOKS_RUN_DIR"),
    ],
)
def test_refuses_hardcoded_path_and_names_variable(repo: Path, literal: str, variable: str):
    _stage(repo, "library/backend/mod.py", f'BASE = "{literal}"\n')
    result = _run_hook(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "COMMIT BLOCKED" in result.stdout
    assert variable in result.stdout
    assert "library/backend/mod.py" in result.stdout


def test_refuses_srv_audiobooks_in_shell_script(repo: Path):
    _stage(repo, "scripts/thing.sh", "#!/bin/bash\ncd /srv/audiobooks/Sources\n")
    assert _run_hook(repo).returncode == 1


def test_clean_file_passes(repo: Path):
    _stage(repo, "library/backend/mod.py", "from config import AUDIOBOOKS_LIBRARY\n")
    result = _run_hook(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "relpath",
    [
        "library/tests/test_x.py",
        "systemd/audiobook-api.service",
        "install.sh",
        "uninstall.sh",
        "lib/audiobook-config.sh",
        "library/config.py",
        "docs/NOTES.md",
    ],
)
def test_skipped_paths_pass(repo: Path, relpath: str):
    _stage(repo, relpath, 'X = "/srv/audiobooks/Library"\nY=/var/lib/audiobooks/db\n')
    result = _run_hook(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_comment_only_mention_passes(repo: Path):
    _stage(repo, "scripts/thing.sh", "#!/bin/bash\n# default is /srv/audiobooks\n")
    assert _run_hook(repo).returncode == 0


def test_dashed_run_name_is_not_the_run_dir(repo: Path):
    _stage(repo, "scripts/thing.sh", "#!/bin/bash\nsystemctl status /run/audiobooks-foo\n")
    assert _run_hook(repo).returncode == 0


def test_only_added_lines_are_judged(repo: Path):
    """Editing a file that already carries a legacy literal must not be blocked."""
    legacy = 'OLD = "/srv/audiobooks/Library"\n'
    _stage(repo, "library/mod.py", legacy)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            "x",
        ],
        cwd=repo,
        check=True,
    )
    _stage(repo, "library/mod.py", legacy + "NEW = 1\n")
    assert _run_hook(repo).returncode == 0
    _stage(repo, "library/mod.py", legacy + 'NEW = "/srv/audiobooks/Sources"\n')
    assert _run_hook(repo).returncode == 1
