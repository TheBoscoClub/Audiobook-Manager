"""install.sh / upgrade.sh: the "project source dependencies" check (bd Audiobook-Manager-2l3).

Both scripts warn when a ``/usr/local/bin/audiobook-*`` link makes production
depend on a tree outside the installed app. The check used to grep the link
target for one host's projects-directory name, so it could never fire on any
other host. It now resolves each link (readlink -f) and flags any whose target
is outside the install prefix.

The check block is extracted from each script and EXECUTED against temp
directories: the literal ``/usr/local/bin`` is replaced by a temp bin dir, and
the prefix variable each script uses ($APP_DIR in install.sh, $target_dir in
upgrade.sh) points at a temp install tree. Nothing on the host is read.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = {"install.sh": "APP_DIR", "upgrade.sh": "target_dir"}


def _check_block(script: str) -> str:
    text = (REPO / script).read_text()
    m = re.search(
        r'^([ \t]*)echo -n "  Checking for project source dependencies\.\.\. "\n.*?^\1fi\n',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert m, f"dependency check not found in {script}"
    helper = re.search(
        r"^_count_links_outside_prefix\(\) \{\n.*?^\}\n", text, re.MULTILINE | re.DOTALL
    )
    return (helper.group(0) if helper else "") + "\n__check() {\n" + m.group(0) + "}\n"


def _run(script: str, bin_dir: Path, prefix: Path, managed: Path | None = None) -> str:
    """Run the check with ``prefix`` as the install tree.

    upgrade.sh checks only the trees whose links it manages (``/opt/audiobooks``
    and ``/usr/local/lib/audiobooks``); ``managed`` (default: ``prefix``) stands
    in for the literal ``/opt/audiobooks`` in that condition.
    """
    body = _check_block(script).replace("/usr/local/bin", str(bin_dir))
    body = body.replace('"/opt/audiobooks"', f'"{managed or prefix}"')
    var = SCRIPTS[script]
    harness = (
        "RED='' GREEN='' YELLOW='' NC=''\n"
        f'{var}="{prefix}"\n'
        "issues_found=0\n" + body + "__check\n"
        'echo "issues=$issues_found"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", harness],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.fixture
def tree(tmp_path: Path) -> dict:
    prefix = tmp_path / "opt" / "audiobooks"
    (prefix / "scripts").mkdir(parents=True)
    checkout = tmp_path / "src" / "my-checkout"
    (checkout / "scripts").mkdir(parents=True)
    for d in (prefix, checkout):
        for name in ("audiobook-api", "audiobook-config"):
            f = d / "scripts" / name
            f.write_text("#!/bin/bash\n")
            f.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return {"prefix": prefix, "checkout": checkout, "bin": bin_dir, "tmp": tmp_path}


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_links_into_prefix_are_ok(script: str, tree: dict) -> None:
    for name in ("audiobook-api", "audiobook-config"):
        os.symlink(tree["prefix"] / "scripts" / name, tree["bin"] / name)
    out = _run(script, tree["bin"], tree["prefix"])
    assert "OK (independent)" in out, out
    assert "WARNING" not in out, out
    assert "issues=0" in out, out


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_link_into_any_checkout_outside_prefix_warns(script: str, tree: dict) -> None:
    """A checkout whose path holds no particular directory name must still be caught."""
    os.symlink(tree["prefix"] / "scripts" / "audiobook-api", tree["bin"] / "audiobook-api")
    os.symlink(tree["checkout"] / "scripts" / "audiobook-config", tree["bin"] / "audiobook-config")
    out = _run(script, tree["bin"], tree["prefix"])
    assert "WARNING: 1 " in out, out
    assert "issues=1" in out, out


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_indirect_link_resolving_outside_prefix_warns(script: str, tree: dict) -> None:
    """A link that points into the prefix but resolves (via a second hop) outside it."""
    hop = tree["prefix"] / "scripts" / "audiobook-hop"
    os.symlink(tree["checkout"] / "scripts" / "audiobook-api", hop)
    os.symlink(hop, tree["bin"] / "audiobook-api")
    out = _run(script, tree["bin"], tree["prefix"])
    assert "WARNING: 1 " in out, out


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_prefix_reached_through_a_symlink_is_ok(script: str, tree: dict) -> None:
    """The prefix is resolved too: links into the real tree match a symlinked prefix."""
    alias = tree["tmp"] / "prefix-alias"
    os.symlink(tree["prefix"], alias)
    os.symlink(tree["prefix"] / "scripts" / "audiobook-api", tree["bin"] / "audiobook-api")
    out = _run(script, tree["bin"], alias)
    assert "OK (independent)" in out, out


def test_upgrade_of_unmanaged_target_skips_the_check(tree: dict) -> None:
    """A user/staging target: the system install's links resolve elsewhere by design."""
    os.symlink(tree["checkout"] / "scripts" / "audiobook-api", tree["bin"] / "audiobook-api")
    user_target = tree["tmp"] / "home" / ".local" / "share" / "audiobooks"
    user_target.mkdir(parents=True)
    out = _run("upgrade.sh", tree["bin"], user_target, managed=tree["prefix"])
    assert "n/a" in out, out
    assert "WARNING" not in out, out
    assert "issues=0" in out, out


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_unresolvable_link_warns(script: str, tree: dict) -> None:
    """A symlink loop cannot be resolved, so it cannot be shown to be inside the prefix."""
    loop_a = tree["bin"] / "audiobook-loop"
    loop_b = tree["tmp"] / "loop-b"
    os.symlink(loop_b, loop_a)
    os.symlink(loop_a, loop_b)
    out = _run(script, tree["bin"], tree["prefix"])
    assert "WARNING: 1 " in out, out


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_sibling_with_prefix_as_name_prefix_warns(script: str, tree: dict) -> None:
    """/opt/audiobooks-old is not inside /opt/audiobooks."""
    sibling = Path(str(tree["prefix"]) + "-old") / "scripts"
    sibling.mkdir(parents=True)
    (sibling / "audiobook-api").write_text("#!/bin/bash\n")
    os.symlink(sibling / "audiobook-api", tree["bin"] / "audiobook-api")
    out = _run(script, tree["bin"], tree["prefix"])
    assert "WARNING: 1 " in out, out
