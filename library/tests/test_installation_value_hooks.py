"""Behavioural tests for the commit-msg and pre-push installation-value guards
(scripts/hooks/commit-msg, scripts/hooks/pre-push, scripts/hooks/lib/) and for
scripts/install-hooks.sh installing all three hooks.

Each test builds throwaway repositories under tmp_path (a bare repository
stands in for the remote) and drives the hooks through real `git commit` and
`git push`, exactly as a contributor would. Synthetic keys only, as in
test_pre_commit_hook.py. HOOKS_DIR_UNDER_TEST / INSTALL_HOOKS_UNDER_TEST point
at other copies (used to prove the tests go red against the pre-change hooks).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_pre_commit_hook import BEADS_BLOCK, SCRUB_MAP, _write_map

pytestmark = pytest.mark.requires_repo_source

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = Path(os.environ.get("HOOKS_DIR_UNDER_TEST", REPO_ROOT / "scripts/hooks"))
INSTALLER = Path(os.environ.get("INSTALL_HOOKS_UNDER_TEST", REPO_ROOT / "scripts/install-hooks.sh"))
# The promotional-trailer scrubber the installed commit-msg carried before this
# guard existed (tracked by beads at .beads/hooks/commit-msg)
SCRUBBER = REPO_ROOT / ".beads/hooks/commit-msg"
KEY = "vm-alpha-01"  # a key of SCRUB_MAP
GITIGNORE = ".claude/local-scrub-map.txt\n"  # as in the real checkout


def _env(extra_path: Path | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if extra_path is not None:
        env["PATH"] = f"{extra_path}:{env['PATH']}"
    return env


def _git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env or _env(),
        capture_output=True,
        text=True,
        check=check,
    )


def _install_copies(repo: Path) -> None:
    """Install the hooks under test by plain copy (the installer has its own tests)."""
    dest = repo / ".git" / "hooks"
    for name in ("pre-commit", "commit-msg", "pre-push"):
        src = HOOKS_DIR / name
        if src.is_file():
            (dest / name).write_text(src.read_text())
            (dest / name).chmod(0o755)
    if (HOOKS_DIR / "lib").is_dir():
        shutil.copytree(HOOKS_DIR / "lib", dest / "lib", dirs_exist_ok=True)


def _write(repo: Path, relpath: str, content: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _commit_all(repo: Path, msg: str) -> None:
    """Commit everything WITHOUT the commit hooks, as cherry-pick/rebase/--no-verify do."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", msg)


def _push(repo: Path, *args: str):
    return _git(repo, "push", "-q", "origin", *args, check=False)


def _remote_has(remote: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "-q", "--verify", ref],
            cwd=remote,
            env=_env(),
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True, env=_env())
    return path


@pytest.fixture
def clone(tmp_path: Path, remote: Path) -> Path:
    """A working repo with hooks installed, `main` already published (clean)."""
    repo = tmp_path / "work"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=_env())
    _git(repo, "remote", "add", "origin", str(remote))
    _install_copies(repo)
    _write(repo, ".gitignore", GITIGNORE)
    _write(repo, ".beads/hooks/commit-msg", SCRUBBER.read_text())
    _write(repo, "README.md", "generic\n")
    _commit_all(repo, "initial")
    assert _push(repo, "main").returncode == 0
    return repo


def _blocked(result) -> None:
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert "PUSH BLOCKED" in result.stderr, out
    assert KEY not in out, "a refusal must never print the value"


# --- commit-msg --------------------------------------------------------------


def test_commit_msg_refuses_message_with_key(clone: Path):
    _write_map(clone)
    _write(clone, "a.txt", "x\n")
    _git(clone, "add", "-A")
    head = _git(clone, "rev-parse", "HEAD").stdout
    result = _git(clone, "commit", "-q", "-m", f"fix\n\ndeployed to {KEY}", check=False)
    assert result.returncode != 0
    assert "commit message contains an installation value" in result.stderr
    assert "commit message line 3" in result.stderr
    assert KEY not in result.stderr
    assert _git(clone, "rev-parse", "HEAD").stdout == head, "no commit may be recorded"


def test_commit_msg_passes_clean_message(clone: Path):
    _write_map(clone)
    _write(clone, "a.txt", "x\n")
    _git(clone, "add", "-A")
    result = _git(clone, "commit", "-q", "-m", "deployed to <test-vm-name>", check=False)
    assert result.returncode == 0, result.stderr
    assert _git(clone, "log", "-1", "--format=%s").stdout.strip() == "deployed to <test-vm-name>"


def _run_commit_msg(repo: Path, text: str):
    msg = repo / ".git" / "COMMIT_EDITMSG"
    msg.write_text(text)
    result = subprocess.run(
        ["bash", str(repo / ".git/hooks/commit-msg"), str(msg)],
        cwd=repo,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return result, msg.read_text()


def test_commit_msg_ignores_git_comment_lines(clone: Path):
    _write_map(clone)
    result, _ = _run_commit_msg(
        clone, f"subject\n# Please enter the commit message\n#\tdeleted:    docs/{KEY}.md\n"
    )
    assert result.returncode == 0, result.stderr


def test_commit_msg_stops_at_verbose_cut_line(clone: Path):
    _write_map(clone)
    text = (
        "subject\n"
        "# ------------------------ >8 ------------------------\n"
        "# Do not modify or remove the line above.\n"
        f"diff --git a/x b/x\n-old {KEY}\n"
    )
    result, _ = _run_commit_msg(clone, text)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("cleanup", ["verbatim", "whitespace"])
def test_commit_msg_stops_at_cut_line_in_every_cleanup_mode(clone: Path, cleanup: str):
    """git truncates at the -v cut line whatever commit.cleanup says."""
    _write_map(clone)
    _git(clone, "config", "commit.cleanup", cleanup)
    text = (
        "subject\n"
        "# ------------------------ >8 ------------------------\n"
        f"diff --git a/x b/x\n-old {KEY}\n"
    )
    result, _ = _run_commit_msg(clone, text)
    assert result.returncode == 0, result.stderr


def test_commit_msg_without_beads_scrubber_still_judges(clone: Path):
    """A checkout without .beads/hooks/commit-msg skips scrubbing, not the value check."""
    (clone / ".beads/hooks/commit-msg").unlink()
    _write_map(clone)
    result, after = _run_commit_msg(clone, "subject\n\nCo-Authored-By: Claude <x@y>\n")
    assert result.returncode == 0, result.stderr
    assert "Co-Authored-By" in after
    result, _ = _run_commit_msg(clone, f"deployed to {KEY}\n")
    assert result.returncode != 0
    assert "COMMIT BLOCKED" in result.stderr


def test_commit_msg_scrubs_promotional_lines(clone: Path):
    _write(clone, "a.txt", "x\n")
    _git(clone, "add", "-A")
    msg = "subject\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    assert _git(clone, "commit", "-q", "-m", msg, check=False).returncode == 0
    recorded = _git(clone, "log", "-1", "--format=%B").stdout
    assert "Co-Authored-By" not in recorded
    assert "body" in recorded


def test_commit_msg_scrubs_before_judging(clone: Path):
    """The value check judges the scrubbed message: a promo line is gone first."""
    _write_map(clone)
    result, after = _run_commit_msg(clone, f"subject\n\nGenerated with Claude Code {KEY}\n")
    assert result.returncode == 0, result.stderr
    assert after == "subject\n"


def test_commit_msg_bad_map_fails_closed(clone: Path):
    _write_map(clone, "regex:([unclosed==>x\n")
    result, _ = _run_commit_msg(clone, "subject\n")
    assert result.returncode != 0
    assert "refusing the commit" in result.stderr


def test_commit_msg_no_map_checks_nothing(clone: Path):
    result, _ = _run_commit_msg(clone, f"deployed to {KEY}\n")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("config", "text", "refused"),
    [
        # core.commentChar ';': a ';' line is git's comment, a '#' line is message
        (("core.commentChar", ";"), f"subject\n;\tdeleted:    docs/{KEY}.md\n", False),
        (("core.commentChar", ";"), f"subject\n# {KEY}\n", True),
        (("core.commentString", "//"), f"subject\n// {KEY}\n", False),
        # auto: the character git picked is unknowable here, so every line counts
        (("core.commentChar", "auto"), f"subject\n# {KEY}\n", True),
        (("core.commentChar", "auto"), f"subject\nauto {KEY}\n", True),
        # cleanup modes that keep '#' lines in the recorded message
        (("commit.cleanup", "verbatim"), f"subject\n# {KEY}\n", True),
        (("commit.cleanup", "whitespace"), f"subject\n# {KEY}\n", True),
        (("commit.cleanup", "scissors"), f"subject\n# {KEY}\n", True),
        (("commit.cleanup", "strip"), f"subject\n# {KEY}\n", False),
    ],
)
def test_commit_msg_follows_git_comment_configuration(
    clone: Path, config: tuple[str, str], text: str, refused: bool
):
    _write_map(clone)
    _git(clone, "config", *config)
    result, _ = _run_commit_msg(clone, text)
    if refused:
        assert result.returncode != 0, result.stderr
        assert "COMMIT BLOCKED" in result.stderr
        assert KEY not in result.stderr
    else:
        assert result.returncode == 0, result.stderr


def test_commit_msg_cut_line_follows_comment_string(clone: Path):
    _write_map(clone)
    _git(clone, "config", "core.commentChar", ";")
    text = (
        "subject\n"
        "; ------------------------ >8 ------------------------\n"
        f"diff --git a/x b/x\n-old {KEY}\n"
    )
    result, _ = _run_commit_msg(clone, text)
    assert result.returncode == 0, result.stderr


def test_hash_line_kept_by_commit_m_is_refused_at_push(clone: Path, remote: Path):
    """git keeps '#' lines of a -m message; commit-msg cannot tell, pre-push judges it."""
    _write_map(clone)
    _write(clone, "a.txt", "x\n")
    _git(clone, "add", "-A")
    assert _git(clone, "commit", "-q", "-m", f"subject\n\n# {KEY}").returncode == 0
    assert f"# {KEY}" in _git(clone, "log", "-1", "--format=%B").stdout
    _blocked(_push(clone, "main"))


@pytest.mark.parametrize("hook", ["pre-commit", "commit-msg", "pre-push"])
def test_missing_library_fails_closed(clone: Path, hook: str):
    shutil.rmtree(clone / ".git/hooks/lib")
    msg = clone / ".git" / "COMMIT_EDITMSG"
    msg.write_text("subject\n")
    result = subprocess.run(
        ["bash", str(clone / ".git/hooks" / hook), str(msg)],
        cwd=clone,
        env=_env(),
        input="refs/heads/main 1111111111111111111111111111111111111111 refs/heads/main "
        "0000000000000000000000000000000000000000\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "installation-value library" in result.stderr


def test_commit_msg_without_message_file_fails_closed(clone: Path):
    result = subprocess.run(
        ["bash", str(clone / ".git/hooks/commit-msg")],
        cwd=clone,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no commit message file given" in result.stderr


# --- pre-push ----------------------------------------------------------------


def test_push_refuses_commit_message_with_key(clone: Path, remote: Path):
    _write_map(clone)
    _write(clone, "a.txt", "x\n")
    _commit_all(clone, f"subject\n\nreached {KEY}")
    sha = _git(clone, "rev-parse", "--short=12", "HEAD").stdout.strip()
    result = _push(clone, "main")
    _blocked(result)
    assert f"commit {sha}: message line 3 contains" in result.stderr
    assert _git(remote, "rev-parse", "main").stdout != _git(clone, "rev-parse", "HEAD").stdout


def test_push_refuses_added_line_with_key(clone: Path):
    _write_map(clone)
    _write(clone, "docs/notes.md", f"deploy to {KEY}\n")
    _commit_all(clone, "notes")
    result = _push(clone, "main")
    _blocked(result)
    assert "added line in docs/notes.md contains" in result.stderr


def test_push_refuses_key_hidden_by_minus_diff_attribute(clone: Path):
    _write_map(clone)
    _write(clone, ".gitattributes", "*.py -diff\n")
    _write(clone, "a.py", f'HOST = "{KEY}"\n')
    _commit_all(clone, "py")
    _blocked(_push(clone, "main"))


@pytest.mark.parametrize("relpath", [f"docs/{KEY}.md", f"{KEY}/empty.txt"])
def test_push_refuses_path_with_key_and_masks_it(clone: Path, relpath: str):
    _write_map(clone)
    _write(clone, relpath, "")
    _commit_all(clone, "add file")
    result = _push(clone, "main")
    _blocked(result)
    assert f"path {relpath.replace(KEY, '<installation value>')} contains" in result.stderr


def test_push_passes_clean_push(clone: Path, remote: Path):
    _write_map(clone)
    _write(clone, "docs/notes.md", "deploy to <test-vm-name> at example.org\n")
    _commit_all(clone, "clean notes")
    result = _push(clone, "main")
    assert result.returncode == 0, result.stderr
    assert _git(remote, "rev-parse", "main").stdout == _git(clone, "rev-parse", "HEAD").stdout


def test_push_no_map_checks_nothing(clone: Path):
    _write(clone, "docs/notes.md", f"deploy to {KEY}\n")
    _commit_all(clone, "notes")
    assert _push(clone, "main").returncode == 0


def _publish_legacy_value(clone: Path) -> None:
    """A value already on the remote, pushed before the map existed."""
    _write(clone, "docs/legacy.md", f"old {KEY}\n")
    _commit_all(clone, "legacy")
    assert _push(clone, "main").returncode == 0
    _write_map(clone)


def test_push_to_existing_ref_judges_only_new_commits(clone: Path):
    _publish_legacy_value(clone)
    _write(clone, "docs/new.md", "generic\n")
    _commit_all(clone, "new")
    result = _push(clone, "main")
    assert result.returncode == 0, result.stderr


def test_push_without_tracking_ref_judges_only_commits_the_remote_lacks(clone: Path):
    """The remote's own tip excludes what it already has, tracking ref or not."""
    _publish_legacy_value(clone)
    _git(clone, "update-ref", "-d", "refs/remotes/origin/main")
    _write(clone, "docs/new.md", "generic\n")
    _commit_all(clone, "new")
    result = _push(clone, "main")
    assert result.returncode == 0, result.stderr


def test_push_new_branch_judges_only_unpublished_commits(clone: Path):
    _publish_legacy_value(clone)
    _git(clone, "checkout", "-q", "-b", "topic")
    _write(clone, "docs/topic.md", "generic\n")
    _commit_all(clone, "topic")
    result = _push(clone, "topic")
    assert result.returncode == 0, result.stderr


def test_push_new_branch_with_key_commit_is_refused(clone: Path, remote: Path):
    _write_map(clone)
    _git(clone, "checkout", "-q", "-b", "topic")
    _write(clone, "docs/topic.md", f"see {KEY}\n")
    _commit_all(clone, "topic")
    _write(clone, "docs/more.md", "generic\n")
    _commit_all(clone, "more")
    _blocked(_push(clone, "topic"))
    assert not _remote_has(remote, "refs/heads/topic")


def test_push_skips_deletions(clone: Path, remote: Path):
    _git(clone, "checkout", "-q", "-b", "old")
    _write(clone, "docs/old.md", f"see {KEY}\n")
    _commit_all(clone, "old")
    assert _push(clone, "old").returncode == 0  # published before the map existed
    _write_map(clone)
    _git(clone, "checkout", "-q", "main")
    result = _push(clone, "--delete", "old")
    assert result.returncode == 0, result.stderr
    assert not _remote_has(remote, "refs/heads/old")


def test_push_bad_map_fails_closed(clone: Path, remote: Path):
    _write_map(clone, "glob:*.example.net==>x\n")
    _write(clone, "a.txt", "x\n")
    _commit_all(clone, "a")
    result = _push(clone, "main")
    assert result.returncode != 0
    assert "refusing the push" in result.stderr
    assert _git(remote, "rev-parse", "main").stdout != _git(clone, "rev-parse", "HEAD").stdout


def test_push_map_not_a_regular_file_fails_closed(clone: Path):
    (clone / ".claude" / "local-scrub-map.txt").mkdir(parents=True)
    _write(clone, "a.txt", "x\n")
    _commit_all(clone, "a")
    result = _push(clone, "main")
    assert result.returncode != 0
    assert "is not a readable regular file" in result.stderr


def test_cherry_pick_of_key_commit_is_caught_at_push(clone: Path):
    _write_map(clone)
    _git(clone, "checkout", "-q", "-b", "scratch")
    _write(clone, "docs/x.md", f"host {KEY}\n")
    _commit_all(clone, "scratch work")  # made without hooks
    picked = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _git(clone, "checkout", "-q", "main")
    _git(clone, "cherry-pick", picked)  # cherry-pick runs no commit hooks
    result = _push(clone, "main")
    _blocked(result)
    assert "added line in docs/x.md" in result.stderr


def test_evil_merge_line_is_refused_and_clean_merge_passes(clone: Path):
    _write_map(clone)
    _git(clone, "checkout", "-q", "-b", "side")
    _write(clone, "docs/side.md", "side\n")
    _commit_all(clone, "side")
    _git(clone, "checkout", "-q", "main")
    _write(clone, "docs/main.md", "main\n")
    _commit_all(clone, "main")
    _git(clone, "merge", "-q", "--no-verify", "--no-edit", "side")
    assert _push(clone, "main").returncode == 0  # a clean merge passes
    _git(clone, "checkout", "-q", "-b", "side2", "HEAD~1")
    _write(clone, "docs/side2.md", "side2\n")
    _commit_all(clone, "side2")
    _git(clone, "checkout", "-q", "main")
    _git(clone, "merge", "-q", "--no-verify", "--no-commit", "side2")
    _write(clone, "docs/main.md", f"main\nresolved on {KEY}\n")  # introduced by the merge itself
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "--no-verify", "--no-edit")
    merge = _git(clone, "rev-parse", "--short=12", "HEAD").stdout.strip()
    result = _push(clone, "main")
    _blocked(result)
    assert f"commit {merge}: added line in docs/main.md" in result.stderr


def test_path_added_by_a_merge_itself_is_refused(clone: Path):
    _write_map(clone)
    _git(clone, "checkout", "-q", "-b", "side")
    _write(clone, "docs/side.md", "side\n")
    _commit_all(clone, "side")
    _git(clone, "checkout", "-q", "main")
    _write(clone, "docs/main.md", "main\n")
    _commit_all(clone, "main")
    _git(clone, "merge", "-q", "--no-verify", "--no-commit", "side")
    _write(clone, f"docs/{KEY}.md", "")  # a new path in no parent
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "--no-verify", "--no-edit")
    merge = _git(clone, "rev-parse", "--short=12", "HEAD").stdout.strip()
    result = _push(clone, "main")
    _blocked(result)
    assert f"commit {merge}: path docs/<installation value>.md contains" in result.stderr


def test_merge_does_not_rejudge_lines_taken_from_a_published_parent(clone: Path):
    """A conflicted merge shows parent lines in its --cc diff; only lines the
    merge itself introduces ("+" in every parent column) are judged."""
    _write(clone, "f.txt", "a\nb\n")
    _commit_all(clone, "base")
    assert _push(clone, "main").returncode == 0
    _git(clone, "checkout", "-q", "-b", "side")
    _write(clone, "f.txt", f"a\nlegacy {KEY}\n")
    _commit_all(clone, "side")
    assert _push(clone, "side").returncode == 0  # published before the map existed
    _git(clone, "checkout", "-q", "main")
    _write(clone, "f.txt", "a\nmain\n")
    _commit_all(clone, "main")
    assert _push(clone, "main").returncode == 0
    _write_map(clone)
    _git(clone, "merge", "-q", "--no-verify", "side", check=False)  # conflicts
    _write(clone, "f.txt", f"a\nmain\nlegacy {KEY}\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "--no-verify", "--no-edit")
    combined = _git(clone, "diff-tree", "--cc", "-U0", "--no-commit-id", "HEAD").stdout
    assert f"+ legacy {KEY}" in combined  # the parent line is really in the combined diff
    result = _push(clone, "main")
    assert result.returncode == 0, result.stderr


def _run_pre_push(repo: Path, line: str):
    return subprocess.run(
        ["bash", str(repo / ".git/hooks/pre-push"), "origin", "unused"],
        cwd=repo,
        env=_env(),
        input=line,
        capture_output=True,
        text=True,
        check=False,
    )


def test_push_of_unknown_local_sha_fails_closed(clone: Path):
    _write_map(clone)
    result = _run_pre_push(clone, f"refs/heads/main {'1' * 40} refs/heads/main {'0' * 40}\n")
    assert result.returncode != 0
    assert "refusing the push" in result.stderr


def test_git_failure_while_reading_a_commit_fails_closed(clone: Path):
    _write_map(clone)
    _write(clone, "docs/x.md", f"host {KEY}\n")
    _commit_all(clone, "x")
    blob = _git(clone, "rev-parse", "HEAD:docs/x.md").stdout.strip()
    # The commit's content becomes unreadable (the work-tree copy goes too: git
    # diff reuses an up-to-date work-tree file in place of the blob)
    (clone / ".git/objects" / blob[:2] / blob[2:]).unlink()
    (clone / "docs/x.md").unlink()
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    result = _run_pre_push(clone, f"refs/heads/main {head} refs/heads/main {'0' * 40}\n")
    assert result.returncode != 0
    assert "refusing the push" in result.stderr


def test_push_refuses_ref_name_with_key(clone: Path):
    _write_map(clone)
    _git(clone, "checkout", "-q", "-b", f"fix-{KEY}")
    result = _push(clone, f"fix-{KEY}")
    _blocked(result)
    assert "pushed ref refs/heads/fix-<installation value> contains" in result.stderr


def test_push_refuses_annotated_tag_message_with_key(clone: Path, remote: Path):
    _write_map(clone)
    _git(clone, "tag", "-a", "v1", "-m", f"released on {KEY}")
    result = _push(clone, "v1")
    _blocked(result)
    assert "message line 1 contains" in result.stderr
    assert not _remote_has(remote, "refs/tags/v1")


def test_push_with_unknown_remote_sha_judges_unpublished_commits(
    clone: Path, remote: Path, tmp_path: Path
):
    """Force-push from a clone that never fetched the remote's newer tip."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True, env=_env())
    _write(other, "docs/other.md", "other\n")
    _commit_all(other, "other")
    assert _git(other, "push", "-q", "origin", "main", check=False).returncode == 0
    _write_map(clone)
    _write(clone, "docs/x.md", f"host {KEY}\n")
    _commit_all(clone, "x")
    result = _push(clone, "--force", "main")
    _blocked(result)
    assert "added line in docs/x.md" in result.stderr


# --- pre-push: only the destination's own tracking refs count ----------------


def _bare(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True, env=_env())
    return path


def _on_origin_only(clone: Path) -> None:
    """A key commit already on origin (pushed before the map existed)."""
    _write(clone, "docs/private.md", f"host {KEY}\n")
    _commit_all(clone, "private")
    assert _push(clone, "main").returncode == 0
    _write_map(clone)


@pytest.mark.parametrize("destination_has_main", [True, False])
def test_commit_on_another_remote_is_judged_when_pushed_to_a_second_remote(
    clone: Path, tmp_path: Path, destination_has_main: bool
):
    public = _bare(tmp_path, "public.git")
    _git(clone, "remote", "add", "public", str(public))
    if destination_has_main:
        assert _git(clone, "push", "-q", "public", "main", check=False).returncode == 0
    _on_origin_only(clone)
    result = _git(clone, "push", "-q", "public", "main", check=False)
    _blocked(result)
    assert "added line in docs/private.md" in result.stderr
    assert (
        _git(clone, "rev-parse", "HEAD").stdout.strip()
        != _git(
            public, "rev-parse", "-q", "--verify", "refs/heads/main", check=False
        ).stdout.strip()
    )


def test_push_to_a_bare_url_judges_commits_on_tracking_refs(clone: Path, tmp_path: Path):
    public = _bare(tmp_path, "public.git")
    _on_origin_only(clone)
    _blocked(_git(clone, "push", "-q", str(public), "main", check=False))
    assert not _remote_has(public, "refs/heads/main")


def test_separate_pushurl_does_not_trust_the_fetch_side_tracking_refs(clone: Path, tmp_path: Path):
    public = _bare(tmp_path, "public.git")
    _on_origin_only(clone)
    _git(clone, "remote", "set-url", "--push", "origin", str(public))
    _blocked(_push(clone, "main"))
    assert not _remote_has(public, "refs/heads/main")


def test_tracking_refs_of_a_nested_remote_name_do_not_count(clone: Path, tmp_path: Path):
    """Remote `origin/mirror` keeps its refs under refs/remotes/origin/mirror/.

    `git remote add` refuses such a name, but a remote set up in the config works.
    """
    mirror = _bare(tmp_path, "mirror.git")
    _git(clone, "config", "remote.origin/mirror.url", str(mirror))
    _git(
        clone, "config", "remote.origin/mirror.fetch", "+refs/heads/*:refs/remotes/origin/mirror/*"
    )
    _write(clone, "docs/private.md", f"host {KEY}\n")
    _commit_all(clone, "private")
    assert _git(clone, "push", "-q", "origin/mirror", "main", check=False).returncode == 0
    assert (
        _git(clone, "rev-parse", "-q", "--verify", "refs/remotes/origin/mirror/main").returncode
        == 0
    )
    _write_map(clone)
    _blocked(_push(clone, "main"))


def test_unreadable_tracking_refs_fail_closed(clone: Path, remote: Path):
    _write_map(clone)
    _git(clone, "pack-refs", "--all")
    with (clone / ".git/packed-refs").open("a") as refs:
        refs.write("not a ref line\n")
    result = subprocess.run(
        ["bash", str(clone / ".git/hooks/pre-push"), "origin", str(remote)],
        cwd=clone,
        env=_env(),
        input=f"refs/heads/main {'1' * 40} refs/heads/main {'0' * 40}\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "cannot list the tracking refs of origin" in result.stderr


# --- pre-push: tags inside tags and merges -----------------------------------


def test_tag_of_a_tag_is_judged_down_the_chain(clone: Path, remote: Path):
    _write_map(clone)
    _git(clone, "tag", "-a", "inner", "-m", f"released on {KEY}")
    _git(clone, "tag", "-a", "outer", "inner", "-m", "clean")
    result = _push(clone, "outer")
    _blocked(result)
    assert "message line 1 contains" in result.stderr
    assert not _remote_has(remote, "refs/tags/outer")


def _signed_tag_merge(clone: Path, tmp_path: Path, tag_msg: str, tag_name: str = "vside") -> None:
    """A real `git merge` of an SSH-signed tag, which git records as a mergetag header."""
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is needed to sign a tag")
    key = tmp_path / "signing"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, env=_env()
    )
    sign = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={key}"]
    _git(clone, "checkout", "-q", "-b", "side")
    _write(clone, "docs/side.md", "generic\n")
    _commit_all(clone, "side")
    _git(clone, *sign, "tag", "-s", tag_name, "-m", tag_msg)
    _git(clone, "checkout", "-q", "main")
    _git(clone, "merge", "-q", "--no-ff", "--no-verify", "-m", "merge", tag_name)
    body = _git(clone, "cat-file", "commit", "HEAD").stdout
    assert "\nmergetag object " in body, body


def test_message_of_a_merged_signed_tag_is_refused(clone: Path, tmp_path: Path):
    _signed_tag_merge(clone, tmp_path, f"released on {KEY}")
    _write_map(clone)
    result = _push(clone, "main")
    _blocked(result)
    assert "merged tag 1: message line 1 contains" in result.stderr


def test_name_of_a_merged_signed_tag_is_refused(clone: Path, tmp_path: Path):
    _signed_tag_merge(clone, tmp_path, "clean", tag_name=f"rel-{KEY}")
    _write_map(clone)
    result = _push(clone, "main")
    _blocked(result)
    assert "merged tag 1: name contains" in result.stderr


def test_clean_merged_signed_tag_passes(clone: Path, tmp_path: Path):
    _signed_tag_merge(clone, tmp_path, "clean")
    _write_map(clone)
    result = _push(clone, "main")
    assert result.returncode == 0, result.stderr


# --- installer ---------------------------------------------------------------

PRE_PUSH_BEADS = BEADS_BLOCK.replace("pre-commit", "pre-push")


@pytest.fixture
def installer_repo(tmp_path: Path, remote: Path) -> Path:
    """Temp repo carrying the installer and the hooks under test, with the hooks
    a beads-enrolled checkout had before: the promotional-trailer scrubber as
    commit-msg and a beads-only pre-push. Never touches the real repository."""
    repo = tmp_path / "inst"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=_env())
    _git(repo, "remote", "add", "origin", str(remote))
    (repo / "scripts").mkdir()
    (repo / "scripts/install-hooks.sh").write_text(INSTALLER.read_text())
    shutil.copytree(HOOKS_DIR, repo / "scripts/hooks")
    (repo / ".beads/hooks").mkdir(parents=True)
    (repo / ".beads/hooks/commit-msg").write_text(SCRUBBER.read_text())
    hooks = repo / ".git/hooks"
    (hooks / "commit-msg").write_text(SCRUBBER.read_text())
    (hooks / "pre-push").write_text("#!/usr/bin/env sh\n\n\n\n\n" + PRE_PUSH_BEADS)
    for name in ("commit-msg", "pre-push"):
        (hooks / name).chmod(0o755)
    return repo


def _run_installer(repo: Path):
    return subprocess.run(
        ["bash", "scripts/install-hooks.sh"],
        cwd=repo,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_installer_installs_three_hooks_and_is_idempotent(installer_repo: Path):
    first = _run_installer(installer_repo)
    assert first.returncode == 0, first.stdout + first.stderr
    hooks = installer_repo / ".git/hooks"
    for name in ("pre-commit", "commit-msg", "pre-push"):
        assert os.access(hooks / name, os.X_OK), name
    assert (hooks / "lib/installation-values.sh").is_file()
    snapshot = {p.name: p.read_text() for p in hooks.iterdir() if p.is_file()}
    second = _run_installer(installer_repo)
    assert second.returncode == 0, second.stdout + second.stderr
    for name in ("pre-commit", "commit-msg", "pre-push", "lib/installation-values.sh"):
        assert f"{name} (already installed)" in second.stdout, second.stdout
    assert {p.name: p.read_text() for p in hooks.iterdir() if p.is_file()} == snapshot


def test_installer_preserves_beads_block_in_pre_push_and_hands_it_the_refs(
    installer_repo: Path,
):
    assert _run_installer(installer_repo).returncode == 0
    text = (installer_repo / ".git/hooks/pre-push").read_text()
    assert text.count("BEGIN BEADS INTEGRATION") == 1
    assert text.count("END BEADS INTEGRATION") == 1
    assert "check_pushed_installation_values" in text
    # the appended beads block still runs after a clean pass, with git's stdin
    stub_dir = installer_repo / "stubbin"
    stub_dir.mkdir()
    seen = installer_repo / "bd-stdin"
    (stub_dir / "bd").write_text(f'#!/bin/bash\ncat >"{seen}"\n')
    (stub_dir / "bd").chmod(0o755)
    env = _env(stub_dir)
    _write_map(installer_repo, SCRUB_MAP)
    _write(installer_repo, "README.md", "generic\n")
    _git(installer_repo, "add", "README.md", env=env)
    _git(installer_repo, "commit", "-q", "-m", "initial", env=env)
    result = _git(installer_repo, "push", "-q", "origin", "main", check=False, env=env)
    assert result.returncode == 0, result.stderr
    head = _git(installer_repo, "rev-parse", "HEAD").stdout.strip()
    assert seen.read_text().split()[:2] == ["refs/heads/main", head]


def test_installer_keeps_scrubbing_in_commit_msg(installer_repo: Path):
    assert _run_installer(installer_repo).returncode == 0
    assert list((installer_repo / ".git/hooks").glob("commit-msg.backup.*"))
    _write_map(installer_repo, SCRUB_MAP)
    _write(installer_repo, "a.txt", "x\n")
    _git(installer_repo, "add", "a.txt")
    msg = "subject\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    assert _git(installer_repo, "commit", "-q", "-m", msg, check=False).returncode == 0
    assert "Co-Authored-By" not in _git(installer_repo, "log", "-1", "--format=%B").stdout
    _write(installer_repo, "b.txt", "y\n")
    _git(installer_repo, "add", "b.txt")
    refused = _git(installer_repo, "commit", "-q", "-m", f"to {KEY}", check=False)
    assert refused.returncode != 0
    assert "commit message contains an installation value" in refused.stderr


def test_installer_refuses_unbalanced_markers_before_changing_anything(installer_repo: Path):
    hooks = installer_repo / ".git/hooks"
    pre_push = hooks / "pre-push"
    pre_push.write_text(pre_push.read_text() + "# --- BEGIN BEADS INTEGRATION v2 ---\n")
    before = {p.name: p.read_text() for p in hooks.iterdir() if p.is_file()}
    result = _run_installer(installer_repo)
    assert result.returncode != 0
    assert "BEADS INTEGRATION markers" in result.stderr
    assert {p.name: p.read_text() for p in hooks.iterdir() if p.is_file()} == before
    assert not (hooks / "pre-commit").exists()
    assert not (hooks / "lib").exists()
