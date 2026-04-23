"""Regression guard for v8.3.7.1 from-github systemd-unit wipe.

Root cause: ``audit_and_cleanup`` fell back to ``${SCRIPT_DIR}/systemd``
when ``${target}/systemd`` was missing (installs never populate that
directory — systemd units live in /etc/systemd/system, not /opt/audiobooks).
When upgrade.sh was copied to /tmp for a --from-github bootstrap,
``SCRIPT_DIR=/tmp`` and ``/tmp/systemd`` didn't exist either. The
orphan-unit loop then matched every installed audiobook-*.service against
an empty source and `rm -f`'d all of them.

Impact: post-upgrade dev + QA VMs lost every unit in /etc/systemd/system/,
leaving services un-runnable until manually restored.

This test pins the three safety invariants added in the fix:

1. ``audit_and_cleanup`` accepts ``project`` as its third arg so the
   release being installed is the authoritative source.
2. The source resolution tries ``$project/systemd`` first, then
   ``$target/systemd``, then ``$SCRIPT_DIR/systemd`` — and each candidate
   is only accepted if it actually contains ``audiobook*.service`` files
   (an empty directory is not a valid source).
3. If NO source has audiobook-*.service files, the orphan loop is skipped
   entirely. Deleting installed units because the canonical list is
   unfindable is worse than leaving a genuinely-obsolete unit behind.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text()


def _audit_body() -> str:
    upgrade = _read("upgrade.sh")
    func_match = re.search(
        r"^audit_and_cleanup\s*\(\)\s*\{\n(.*?)^\}",
        upgrade,
        re.DOTALL | re.MULTILINE,
    )
    assert func_match, "audit_and_cleanup function not found in upgrade.sh"
    return func_match.group(1)


def test_audit_accepts_project_as_third_arg():
    """audit_and_cleanup must accept $project so it has a trustworthy source.

    The fallback chain without $project (target/systemd → SCRIPT_DIR/systemd)
    is unsafe when upgrade.sh is run standalone from /tmp/.
    """
    body = _audit_body()
    assert re.search(r'local\s+project=["\']?\$\{?3(:-)?', body), (
        "audit_and_cleanup must accept a third positional arg named 'project' "
        "so the release being installed is the authoritative systemd source. "
        "Without it, from-github bootstrap (upgrade.sh in /tmp/) silently "
        "nukes every installed audiobook-*.service."
    )


def test_audit_tries_project_before_target_before_script_dir():
    """Source resolution must prefer $project, then $target, then SCRIPT_DIR."""
    body = _audit_body()
    # Collect the candidate ordering in the resolver loop.
    candidate_match = re.search(
        r'for\s+_candidate\s+in\s+(.+?);\s*do', body, re.DOTALL
    )
    assert candidate_match, (
        "audit_and_cleanup no longer resolves systemd source via an ordered "
        "for-loop — the from-github wipe regression is unprotected."
    )
    candidates = candidate_match.group(1)
    # $project must appear before $target, and $target before $SCRIPT_DIR
    project_pos = candidates.find("project")
    target_pos = candidates.find("target")
    script_dir_pos = candidates.find("SCRIPT_DIR")
    assert project_pos != -1 and target_pos != -1 and script_dir_pos != -1, (
        "source-resolution loop must include project, target, and SCRIPT_DIR "
        "candidates; one or more is missing"
    )
    assert project_pos < target_pos < script_dir_pos, (
        "source-resolution order must be project → target → SCRIPT_DIR "
        "(got: project=%d, target=%d, SCRIPT_DIR=%d)"
        % (project_pos, target_pos, script_dir_pos)
    )


def test_audit_rejects_empty_candidate_dirs():
    """Each candidate must actually contain audiobook-*.service files.

    An empty directory is indistinguishable from a missing one for orphan
    detection; requiring at least one audiobook unit prevents the false
    positive that wiped dev + QA.
    """
    body = _audit_body()
    assert re.search(
        r'compgen\s+-G\s+["\']?\$\{?_candidate\}?/audiobook\*\.service',
        body,
    ), (
        "source-resolution must verify each candidate contains "
        "audiobook*.service files (compgen -G glob check). Without this, "
        "an empty $project/systemd dir is treated as valid and every "
        "installed unit is declared orphaned."
    )


def test_orphan_loop_skipped_when_no_trusted_source():
    """With project_systemd_dir empty, the destructive loop must be skipped."""
    body = _audit_body()
    # The body has a clear guard wrapping the orphan while-loop.
    guard_match = re.search(
        r'if\s+\[\[\s+-n\s+"?\$project_systemd_dir"?\s+\]\]\s*;\s*then\s*\n'
        r'(.*?)'
        r'\n\s*fi\s*\n\s*\n\s*#\s*---\s*\(c2\)',
        body,
        re.DOTALL,
    )
    assert guard_match, (
        "orphan-systemd-unit loop is no longer wrapped in a "
        "[[ -n $project_systemd_dir ]] guard. Without the guard, an empty "
        "source falls through to the `[[ ! -f ${project_systemd_dir}/$unit ]]`"
        " check which evaluates to the path-root (e.g. /audiobook-api.service)"
        " — always missing — and every installed unit gets rm -f'd."
    )
    guarded_region = guard_match.group(1)
    # The rm -f must live inside the guarded region, not outside it.
    assert "rm -f" in guarded_region, (
        "rm -f of orphaned units must execute inside the "
        "[[ -n $project_systemd_dir ]] guard"
    )


def test_caller_passes_project_to_audit():
    """do_upgrade must pass $project as the third arg to audit_and_cleanup."""
    upgrade = _read("upgrade.sh")
    # The single audit_and_cleanup call site inside do_upgrade must include
    # "$project" as the third positional argument.
    assert re.search(
        r'audit_and_cleanup\s+"\$target"\s+"\$use_sudo"\s+"\$project"',
        upgrade,
    ), (
        "do_upgrade's call to audit_and_cleanup must pass $project as the "
        "third arg. Otherwise the from-github bootstrap (SCRIPT_DIR=/tmp) "
        "wipes every installed systemd unit."
    )
