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

# The hook and its installer live only in the project checkout
pytestmark = pytest.mark.requires_repo_source

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
        "vm-test-manifest.example.json",
    ],
)
def test_skipped_paths_pass(repo: Path, relpath: str):
    _stage(repo, relpath, 'X = "/srv/audiobooks/Library"\nY=/var/lib/audiobooks/db\n')
    result = _run_hook(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "relpath", ["vm-test-manifest.json", "config/vm-test-manifest.example.json"]
)
def test_manifest_exemption_is_exact_path(repo: Path, relpath: str):
    _stage(repo, relpath, '{"db_path": "/var/lib/audiobooks/db/audiobooks.db"}\n')
    assert _run_hook(repo).returncode != 0


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


# --- bypass regressions (review of af556042) --------------------------------

LITERAL = 'p = "/srv/audiobooks/x"\n'


def _commit(repo: Path, msg: str = "x") -> None:
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
            msg,
        ],
        cwd=repo,
        check=True,
    )


def test_rename_with_added_literal_is_scanned(repo: Path):
    _stage(repo, "old.py", "x = 1\ny = 2\nz = 3\nw = 4\n")
    _commit(repo)
    subprocess.run(["git", "mv", "old.py", "new.py"], cwd=repo, check=True)
    (repo / "new.py").write_text("x = 1\ny = 2\nz = 3\nw = 4\n" + LITERAL)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    assert _run_hook(repo).returncode == 1


def test_snapshots_word_on_same_line_does_not_hide_literal(repo: Path):
    _stage(repo, "a.py", 'p = "/srv/audiobooks"; s = ".snapshots"\n')
    assert _run_hook(repo).returncode == 1


def test_line_starting_with_plus_plus_is_scanned(repo: Path):
    _stage(repo, "a.js", '++n; p = "/srv/audiobooks"\n')
    assert _run_hook(repo).returncode == 1


def test_color_ui_always_does_not_blind_hook(repo: Path):
    subprocess.run(["git", "config", "color.ui", "always"], cwd=repo, check=True)
    subprocess.run(["git", "config", "color.diff", "always"], cwd=repo, check=True)
    _stage(repo, "a.py", LITERAL)
    assert _run_hook(repo).returncode == 1


def test_minus_diff_attribute_does_not_blind_hook(repo: Path):
    (repo / ".gitattributes").write_text("*.py -diff\n")
    _stage(repo, "a.py", LITERAL)
    assert _run_hook(repo).returncode == 1


def test_docs_directory_code_is_not_skipped(repo: Path):
    _stage(repo, "docs/x.py", LITERAL)
    assert _run_hook(repo).returncode == 1


def test_git_failure_fails_closed(repo: Path, tmp_path_factory: pytest.TempPathFactory):
    _stage(repo, "a.py", "x = 1\n")
    bad_index = tmp_path_factory.mktemp("badidx") / "index"
    bad_index.write_text("this is not a git index\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_INDEX_FILE"] = str(bad_index)
    result = subprocess.run(
        ["bash", str(HOOK)], cwd=repo, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "refusing the commit" in result.stderr


# --- installer + appended-block cooperation ----------------------------------

BEADS_BLOCK = """# --- BEGIN BEADS INTEGRATION v1.0.3 ---
if command -v bd >/dev/null 2>&1; then
  bd hooks run pre-commit "$@"
fi
# --- END BEADS INTEGRATION v1.0.3 ---
"""


@pytest.fixture
def installed_repo(tmp_path: Path) -> Path:
    """Temp repo with scripts/install-hooks.sh + hook and a pre-existing hook
    that already carries a beads block. Never touches the real repository."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=env)
    scripts = tmp_path / "scripts"
    (scripts / "hooks").mkdir(parents=True)
    (scripts / "install-hooks.sh").write_text((REPO_ROOT / "scripts/install-hooks.sh").read_text())
    (scripts / "hooks" / "pre-commit").write_text(HOOK.read_text())
    old = tmp_path / ".git/hooks/pre-commit"
    old.write_text("#!/bin/bash\n# old hook\nexit 0\n\n" + BEADS_BLOCK)
    old.chmod(0o755)
    return tmp_path


def _install(repo: Path) -> None:
    subprocess.run(["bash", "scripts/install-hooks.sh"], cwd=repo, check=True, capture_output=True)


def test_installer_preserves_beads_block(installed_repo: Path):
    _install(installed_repo)
    text = (installed_repo / ".git/hooks/pre-commit").read_text()
    assert text.count("BEGIN BEADS INTEGRATION") == 1
    assert text.count("END BEADS INTEGRATION") == 1
    assert "check_hardcoded_paths" in text
    assert "# old hook" not in text
    assert list((installed_repo / ".git/hooks").glob("pre-commit.backup.*"))
    # idempotent: a second run changes nothing and does not duplicate the block
    _install(installed_repo)
    assert (installed_repo / ".git/hooks/pre-commit").read_text() == text


def test_appended_block_runs_after_clean_pass_and_not_after_refusal(installed_repo: Path):
    _install(installed_repo)
    stub_dir = installed_repo / "stubbin"
    stub_dir.mkdir()
    marker = installed_repo / "bd-ran"
    stub = stub_dir / "bd"
    stub.write_text(f'#!/bin/bash\ntouch "{marker}"\n')
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]

    _stage(installed_repo, "ok.py", "x = 1\n")
    ok = subprocess.run(git, cwd=installed_repo, env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert marker.exists(), "appended beads block did not run after a clean pass"

    marker.unlink()
    _stage(installed_repo, "bad.py", LITERAL)
    bad = subprocess.run(git, cwd=installed_repo, env=env, capture_output=True, text=True)
    assert bad.returncode != 0
    assert "COMMIT BLOCKED" in bad.stderr


def test_type_change_to_symlink_is_scanned(repo: Path):
    _stage(repo, "t", "plain\n")
    _commit(repo)
    (repo / "t").unlink()
    (repo / "t").symlink_to("/srv/audiobooks/x")
    subprocess.run(["git", "add", "t"], cwd=repo, check=True)
    assert _run_hook(repo).returncode == 1


def test_installer_preserves_every_beads_block(installed_repo: Path):
    hook = installed_repo / ".git/hooks/pre-commit"
    second = BEADS_BLOCK.replace("v1.0.3", "v9.9.9")
    hook.write_text(hook.read_text() + "\n" + second)
    _install(installed_repo)
    text = hook.read_text()
    assert text.count("BEGIN BEADS INTEGRATION") == 2
    assert text.count("END BEADS INTEGRATION") == 2
    assert "v1.0.3" in text
    assert "v9.9.9" in text


def test_installer_refuses_unbalanced_beads_markers(installed_repo: Path):
    hook = installed_repo / ".git/hooks/pre-commit"
    original = hook.read_text() + "# --- BEGIN BEADS INTEGRATION v2 ---\n"
    hook.write_text(original)
    result = subprocess.run(
        ["bash", "scripts/install-hooks.sh"], cwd=installed_repo, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "BEADS INTEGRATION markers" in result.stderr
    assert hook.read_text() == original
