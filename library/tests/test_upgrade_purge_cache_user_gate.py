"""
Regression tests for upgrade.sh purge_cloudflare_cache (inline-curl design).

Design: purge_cloudflare_cache purges the Cloudflare CDN cache INLINE via curl
rather than delegating to a standalone CLI script. The operator-specific
`audiobook-purge-cache` script was removed from the generic repo; the only
remaining purge pathways are this inline function (used by upgrade.sh after
deploying web assets) and the `/api/system/purge-cache` API endpoint.

The inline function (bd Audiobook-Manager-6lv):
  - reads the SAME installation keys as the API endpoint — CF_ZONE_ID plus
    CLOUDFLARE_PURGE_TOKEN or a CLOUDFLARE_PURGE_TOKEN_FILE pointer — each
    from the environment, else from the installed audiobooks.conf
    (AUDIOBOOKS_CONF overrides the path),
  - never reads anything under a user's home directory,
  - skips gracefully (return 0) when the zone or the token is not configured,
  - hands the token to curl on stdin (-K -), never in argv,
  - otherwise POSTs {"purge_everything":true} to the Cloudflare zone
    purge_cache endpoint and treats any failure as non-fatal.

The static tests grep the function; the behavioural tests EXECUTE it with
``curl`` stubbed on PATH (the stub records its argv and stdin and never calls
curl), HOME pointed at a temp dir holding a decoy credential store, and the
config file pointed at a temp file.

bd Audiobook-Manager-obtt: a CLI upgrade by an ordinary user, who cannot read
the installed config (0640 root:audiobooks) or the token file (0400
audiobooks), reads them through ``sudo -n`` (upgrade.sh _purge_privileged) and
purges. ``sudo`` is ALSO stubbed on PATH in every behavioural test, so no test
ever reaches the real sudo: the stub records each invocation, refuses by
default (as ``sudo -n`` does without a cached credential), and in "allow" mode
stands in for root by making the listed mode-000 files readable for the length
of the one command it runs. It refuses any call without ``-n`` first, so a
prompting call can never pass.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).parent.parent.parent
UPGRADE_SH = PROJECT_ROOT / "upgrade.sh"

DECOY_TOKEN = "decoy-token-from-home-123"
DECOY_ZONE = "decoyzone0123"


def _extract(name: str, text: str) -> str | None:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    return match.group(0) if match else None


def _read_purge_function() -> str:
    """Extract the purge_cloudflare_cache function body from upgrade.sh."""
    text = UPGRADE_SH.read_text()
    match = re.search(
        r"^purge_cloudflare_cache\(\) \{\n(.*?)^\}\n",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "purge_cloudflare_cache() not found in upgrade.sh"
    return match.group(1)


def _code_only(body: str) -> str:
    """Drop shell comments so assertions read code, not the prose explaining it."""
    return "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())


def test_purge_cloudflare_cache_function_exists():
    """upgrade.sh must define purge_cloudflare_cache for both local and remote upgrade flows."""
    body = _read_purge_function()
    assert len(body) > 100, "purge_cloudflare_cache function is suspiciously short"


def test_purge_is_inline_curl_not_script_delegation():
    """
    The function must purge INLINE via curl against the Cloudflare zone
    purge_cache endpoint — it must NOT delegate to the removed
    audiobook-purge-cache CLI script, nor probe/sudo for it.
    """
    body = _read_purge_function()
    assert "curl" in body, "purge_cloudflare_cache must do an inline curl"
    assert "/zones/" in body and "purge_cache" in body, (
        "purge_cloudflare_cache must POST to the Cloudflare "
        ".../zones/<id>/purge_cache endpoint inline"
    )
    # The operator-specific CLI script is gone — no references may remain.
    assert "audiobook-purge-cache" not in body, (
        "purge_cloudflare_cache must not reference the removed audiobook-purge-cache CLI script"
    )
    assert "command -v audiobook-purge-cache" not in body, (
        "purge_cloudflare_cache must not probe for the removed CLI script"
    )
    assert "sudo -u audiobooks" not in body, (
        "purge_cloudflare_cache no longer drops privileges to the audiobooks "
        "user — it purges inline as the calling operator"
    )


def test_purge_skips_gracefully_when_credentials_missing():
    """Missing Cloudflare credentials must short-circuit with a non-fatal return 0."""
    body = _read_purge_function()
    # Assert the BEHAVIOUR (guard on an empty credential, log a skip, return 0),
    # not the credential's name. This assertion previously required the literal
    # "no credentials" and broke when the function migrated off the revoked
    # Global API Key to the scoped Zone > Cache Purge token in v8.4.3 — a test
    # that pins wording obstructs the very migration it should be protecting.
    assert "cache purge skipped" in body.lower(), (
        "purge_cloudflare_cache must log a skip message when the credential is missing"
    )
    assert "-z " in body, (
        "purge_cloudflare_cache must guard on an empty credential before calling the API"
    )
    assert "return 0" in body, (
        "purge_cloudflare_cache must return 0 (non-fatal) on the missing-credential path"
    )
    # And it must NOT have regressed to the full-account Global API Key.
    # Strip shell comments first: the migration DOCUMENTS itself by naming the
    # header it removed, and asserting on raw text flags that explanation.
    assert "X-Auth-Key" not in _code_only(body), (
        "purge_cloudflare_cache must use the scoped Bearer token, not a Global API Key"
    )


def test_purge_skips_gracefully_when_zone_id_missing():
    """Missing CF_ZONE_ID must short-circuit with a non-fatal return 0."""
    body = _read_purge_function()
    assert "CF_ZONE_ID not set" in body, (
        "purge_cloudflare_cache must log a skip message when CF_ZONE_ID is unset"
    )
    # CF_ZONE_ID may be sourced from the system config when not in the environment.
    assert "/etc/audiobooks/audiobooks.conf" in body, (
        "purge_cloudflare_cache must fall back to /etc/audiobooks/audiobooks.conf "
        "for CF_ZONE_ID when it is not set in the environment"
    )


def test_purge_reads_no_home_directory_store():
    """
    bd Audiobook-Manager-6lv: the function used to source a credential store
    from the invoking user's home (SUDO_USER's home under sudo) and read that
    store's own variable name. Configuration is the installation's: none of
    those mechanisms may come back.
    """
    code = _code_only(_read_purge_function())
    for forbidden in ("SUDO_USER", "getent passwd", "$HOME", "real_home", "CF_KEYS_FILE"):
        assert forbidden not in code, f"purge_cloudflare_cache must not use {forbidden}"
    assert "source " not in code, "purge_cloudflare_cache must not source any file"
    assert "CF_TOKEN_CACHE_PURGE" not in code, (
        "purge_cloudflare_cache must read the API's key CLOUDFLARE_PURGE_TOKEN, "
        "not a credential store's own variable name"
    )


# ── behavioural tests: the function is executed against stubs ──────────────


def _harness(tmp_path: Path) -> dict:
    """A curl stub on PATH, a HOME holding a decoy store, scratch paths."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    args_log = tmp_path / "curl-args"
    stdin_log = tmp_path / "curl-stdin"
    env_log = tmp_path / "curl-env"
    # The stub records its environment, argv and stdin, and answers; it never
    # invokes curl (or its own name).
    curl = stub_dir / "curl"
    curl.write_text(
        "#!/bin/bash\n"
        f'/usr/bin/env >> "{env_log}"\n'
        f'printf "%s\\n" "$@" >> "{args_log}"\n'
        f'/usr/bin/cat >> "{stdin_log}"\n'
        "echo '{\"success\":true}'\n"
    )
    curl.chmod(0o755)

    # The sudo stub. STUB_SUDO_MODE=allow runs the command with every file in
    # STUB_SUDO_UNLOCK (colon-separated) made readable for that one command;
    # allow-true-only runs `true` and refuses every other command with exit 1;
    # anything else refuses with sudo -n's message and exit status. It never
    # invokes sudo (or its own name): the command is resolved past this
    # directory, which holds only the two stubs.
    sudo_log = tmp_path / "sudo-calls"
    sudo = stub_dir / "sudo"
    sudo.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{sudo_log}"\n'
        'if [[ "$1" != "-n" ]]; then echo "stub: refusing a promptable sudo" >&2; exit 97; fi\n'
        "shift\n"
        'if [[ "$1" == "sudo" ]]; then exit 98; fi\n'
        # allow-true-only: a sudoers rule that permits `true` and nothing else.
        # Real sudo refuses such a command with exit 1 — grep's "no match" code.
        'if [[ "${STUB_SUDO_MODE:-}" == "allow-true-only" && "$1" != "true" ]]; then\n'
        '    echo "Sorry, user stub is not allowed to execute $1 as root." >&2; exit 1\n'
        "fi\n"
        'if [[ "${STUB_SUDO_MODE:-refuse}" != "allow" && "${STUB_SUDO_MODE:-}" != "allow-true-only" ]]; then\n'
        '    echo "sudo: a password is required" >&2; exit 1\n'
        "fi\n"
        'IFS=: read -r -a unlock <<< "${STUB_SUDO_UNLOCK:-}"\n'
        'for f in "${unlock[@]}"; do [[ -n "$f" ]] && /usr/bin/chmod 600 "$f"; done\n'
        '"$@"; rc=$?\n'
        'for f in "${unlock[@]}"; do [[ -n "$f" ]] && /usr/bin/chmod 000 "$f"; done\n'
        'exit "$rc"\n'
    )
    sudo.chmod(0o755)

    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    sourced_marker = tmp_path / "decoy-was-sourced"
    # The path the pre-6lv code read. If anything sources it, the marker appears;
    # if anything reads its values, the decoy token reaches the curl stub.
    (home / ".config" / "api-keys.env").write_text(
        f'/usr/bin/touch "{sourced_marker}"\n'
        f"CF_TOKEN_CACHE_PURGE={DECOY_TOKEN}\n"
        f"CLOUDFLARE_PURGE_TOKEN={DECOY_TOKEN}\n"
        f"CF_ZONE_ID={DECOY_ZONE}\n"
    )
    return {
        "bin": stub_dir,
        "home": home,
        "args": args_log,
        "stdin": stdin_log,
        "sudo": sudo_log,
        "curl_env": env_log,
        "sourced": sourced_marker,
        "conf": tmp_path / "audiobooks.conf",
    }


def _run_purge(h: dict, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    text = UPGRADE_SH.read_text()
    funcs = [
        f
        for f in (
            _extract("_purge_privileged", text),
            _extract("_conf_key_value", text),
            _extract("_purge_conf_read", text),
            _extract("purge_cloudflare_cache", text),
        )
        if f
    ]
    assert any("purge_cloudflare_cache" in f for f in funcs), "purge_cloudflare_cache() not found"
    script = (
        # upgrade.sh runs under `set -e`: a failing command inside the
        # function would abort the whole upgrade, and "rc=0" would never print.
        "set -e\n"
        "RED='' GREEN='' YELLOW='' BLUE='' NC=''\n"
        + "\n".join(funcs)
        + '\npurge_cloudflare_cache\necho "rc=$?"\n'
    )
    env = {
        "PATH": f"{h['bin']}:/usr/bin:/bin",
        "HOME": str(h["home"]),
        "AUDIOBOOKS_CONF": str(h["conf"]),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def _curl_seen(h: dict) -> tuple[str, str]:
    args = h["args"].read_text() if h["args"].exists() else ""
    stdin = h["stdin"].read_text() if h["stdin"].exists() else ""
    return args, stdin


def test_unconfigured_purge_is_skipped_and_reads_nothing_under_home(tmp_path):
    """No config anywhere → skipped, rc 0, curl never called, HOME untouched."""
    h = _harness(tmp_path)
    proc = _run_purge(h)
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "skipped" in proc.stdout, proc.stdout
    assert not h["sourced"].exists(), "the home-directory decoy store was sourced"
    assert DECOY_TOKEN not in args + stdin, "the decoy token from HOME reached curl"
    assert DECOY_ZONE not in args, "the decoy zone from HOME reached curl"
    assert args == "", f"curl was called with no configuration: {args!r}"


def test_zone_without_token_is_skipped_and_home_store_ignored(tmp_path):
    """Zone configured, no token → skipped; the decoy token in HOME is never used."""
    h = _harness(tmp_path)
    h["conf"].write_text('CF_ZONE_ID="abc123"\n')
    proc = _run_purge(h)
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "skipped" in proc.stdout, proc.stdout
    assert not h["sourced"].exists(), "the home-directory decoy store was sourced"
    assert DECOY_TOKEN not in args + stdin, "the decoy token from HOME reached curl"
    assert args == ""


def test_configured_token_file_is_used_and_kept_out_of_argv(tmp_path):
    """CF_ZONE_ID + CLOUDFLARE_PURGE_TOKEN_FILE in the config → purge with that token."""
    h = _harness(tmp_path)
    token_file = tmp_path / "cloudflare-purge-token"
    token_file.write_text("tokFromConfiguredFile456\n")
    h["conf"].write_text(
        f'# installed config\nCF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE="{token_file}"\n'
    )
    proc = _run_purge(h)
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "CDN cache purged" in proc.stdout, proc.stdout
    assert "https://api.cloudflare.com/client/v4/zones/abc123/purge_cache" in args
    assert "Authorization: Bearer tokFromConfiguredFile456" in stdin
    assert "tokFromConfiguredFile456" not in args, "the token must not be in curl's argv"
    assert DECOY_TOKEN not in args + stdin
    assert not h["sourced"].exists()


def test_environment_keys_win_and_inline_token_beats_file(tmp_path):
    """Env over config file; inline CLOUDFLARE_PURGE_TOKEN over the _FILE pointer."""
    h = _harness(tmp_path)
    token_file = tmp_path / "cloudflare-purge-token"
    token_file.write_text("tokFromFile\n")
    h["conf"].write_text('CF_ZONE_ID="confzone"\n')
    proc = _run_purge(
        h,
        {
            "CF_ZONE_ID": "envzone",
            "CLOUDFLARE_PURGE_TOKEN": "tokInline789",
            "CLOUDFLARE_PURGE_TOKEN_FILE": str(token_file),
        },
    )
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "/zones/envzone/purge_cache" in args
    assert "Bearer tokInline789" in stdin
    assert "tokFromFile" not in stdin


def test_unreadable_token_file_is_skipped_nonfatal(tmp_path):
    h = _harness(tmp_path)
    h["conf"].write_text(
        f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE="{tmp_path / "absent"}"\n'
    )
    proc = _run_purge(h)
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "not readable" in proc.stdout, proc.stdout
    assert args == ""


def test_token_file_read_error_does_not_abort_under_set_e(tmp_path):
    """A pointer at something unreadable as a file (a directory) must not kill the upgrade."""
    h = _harness(tmp_path)
    as_dir = tmp_path / "token-is-a-dir"
    as_dir.mkdir()
    h["conf"].write_text(f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE="{as_dir}"\n')
    proc = _run_purge(h)
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "skipped" in proc.stdout, proc.stdout
    assert args == ""


@pytest.mark.parametrize("zone", ["abc/../../x", "abc 123", "abc;touch"])
def test_malformed_zone_id_is_skipped(tmp_path, zone):
    h = _harness(tmp_path)
    proc = _run_purge(h, {"CF_ZONE_ID": zone, "CLOUDFLARE_PURGE_TOKEN": "tok"})
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "skipped" in proc.stdout
    assert args == ""


def test_token_that_could_break_the_curl_config_is_refused(tmp_path):
    h = _harness(tmp_path)
    proc = _run_purge(
        h, {"CF_ZONE_ID": "abc123", "CLOUDFLARE_PURGE_TOKEN": 'x"\nurl = "http://evil'}
    )
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "skipped" in proc.stdout
    assert args == "" and stdin == ""


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file; the case cannot arise")
def test_unreadable_config_is_reported_not_mistaken_for_unset(tmp_path):
    """
    A CLI upgrade runs as the invoking user; the installed config is 0640
    root:audiobooks. A config that exists but cannot be read must be named as
    such — not reported as "CF_ZONE_ID not set" while the key is set.
    """
    h = _harness(tmp_path)
    h["conf"].write_text('CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN="tokInConf"\n')
    h["conf"].chmod(0o000)
    try:
        proc = _run_purge(h)
    finally:
        h["conf"].chmod(0o600)
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert f"{h['conf']} not readable by" in proc.stdout, proc.stdout
    assert "not set" not in proc.stdout, proc.stdout
    assert args == ""


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file; the case cannot arise")
def test_unreadable_config_with_zone_from_env_names_the_config(tmp_path):
    """Zone from the environment, token only in the unreadable config → name the config."""
    h = _harness(tmp_path)
    h["conf"].write_text('CLOUDFLARE_PURGE_TOKEN="tokInConf"\n')
    h["conf"].chmod(0o000)
    try:
        proc = _run_purge(h, {"CF_ZONE_ID": "abc123"})
    finally:
        h["conf"].chmod(0o600)
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert f"{h['conf']} not readable by" in proc.stdout, proc.stdout
    assert "configured)" not in proc.stdout, proc.stdout
    assert args == ""


def test_spaced_assignments_are_read_like_the_api_reads_them(tmp_path):
    """`KEY = "value"` (the style etc/audiobooks.conf.example uses) is honoured."""
    h = _harness(tmp_path)
    token_file = tmp_path / "cloudflare-purge-token"
    token_file.write_text("tokSpaced321\n")
    h["conf"].write_text(f'CF_ZONE_ID = "abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE = "{token_file}"\n')
    proc = _run_purge(h)
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "/zones/abc123/purge_cache" in args, proc.stdout
    assert "Bearer tokSpaced321" in stdin


def test_token_key_does_not_match_the_file_pointer_key(tmp_path):
    """CLOUDFLARE_PURGE_TOKEN must not pick up the CLOUDFLARE_PURGE_TOKEN_FILE line."""
    h = _harness(tmp_path)
    token_file = tmp_path / "cloudflare-purge-token"
    token_file.write_text("tokFromFile654\n")
    h["conf"].write_text(f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE = "{token_file}"\n')
    proc = _run_purge(h)
    _, stdin = _curl_seen(h)
    assert "Bearer tokFromFile654" in stdin, proc.stdout


# ── bd Audiobook-Manager-obtt: root-only files are read through sudo -n ─────

ROOT_ONLY = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a mode-000 file; the case cannot arise"
)
FAKE_TOKEN = "fake-purge-token-obtt"


def _sudo_calls(h: dict) -> list[str]:
    return h["sudo"].read_text().splitlines() if h["sudo"].exists() else []


def _root_only(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o000)
    return path


def _allow(*paths: Path) -> dict:
    return {"STUB_SUDO_MODE": "allow", "STUB_SUDO_UNLOCK": ":".join(str(p) for p in paths)}


def _assert_token_confined(h: dict, proc: subprocess.CompletedProcess, token: str) -> None:
    """The token reached curl on stdin only: not in any argv, output, or environment."""
    args, stdin = _curl_seen(h)
    assert f"Authorization: Bearer {token}" in stdin, stdin
    assert token not in args, "the token must not be in curl's argv"
    assert all(token not in call for call in _sudo_calls(h)), "the token is in sudo's argv"
    assert token not in proc.stdout + proc.stderr, "the token was printed"
    env = h["curl_env"].read_text() if h["curl_env"].exists() else ""
    assert token not in env, "the token was exported into curl's environment"


@ROOT_ONLY
def test_unreadable_config_is_read_through_sudo_n_and_purges(tmp_path):
    """Config 0640 root:audiobooks, user not in the group, sudo -n works → purge."""
    h = _harness(tmp_path)
    _root_only(
        h["conf"],
        f'OTHER_SECRET="fake-unrelated-value"\nCF_ZONE_ID="rootzone1"\n'
        f'CLOUDFLARE_PURGE_TOKEN="{FAKE_TOKEN}"\n',
    )
    proc = _run_purge(h, _allow(h["conf"]))
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "CDN cache purged" in proc.stdout, proc.stdout
    assert "https://api.cloudflare.com/client/v4/zones/rootzone1/purge_cache" in args
    _assert_token_confined(h, proc, FAKE_TOKEN)
    calls = _sudo_calls(h)
    assert calls, "sudo was never invoked for an unreadable config"
    assert all(c.startswith("-n ") for c in calls), f"a promptable sudo was run: {calls}"
    # The privileged read is a grep for one key at a time — never the whole
    # file — plus the stdin sentinel that proves grep ran (_conf_key_value).
    reads = [c for c in calls if c != "-n true"]
    key_read = re.compile(
        r"^-n grep -h -E -e \^\[\[:space:\]\]\*[A-Z_]+\[\[:space:\]\]\*= "
        r"-e \^__audiobooks_conf_read_ok__\$ -- " + re.escape(str(h["conf"])) + r" -$"
    )
    assert reads and all(key_read.match(c) for c in reads), calls
    assert "fake-unrelated-value" not in proc.stdout + proc.stderr


@ROOT_ONLY
def test_unreadable_config_and_token_file_both_read_through_sudo_n(tmp_path):
    """The documented layout: 0640 config pointing at a 0400 /run token file."""
    h = _harness(tmp_path)
    token_file = _root_only(tmp_path / "cloudflare-purge-token", f"{FAKE_TOKEN}\n")
    _root_only(h["conf"], f'CF_ZONE_ID="rootzone2"\nCLOUDFLARE_PURGE_TOKEN_FILE="{token_file}"\n')
    proc = _run_purge(h, _allow(h["conf"], token_file))
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "/zones/rootzone2/purge_cache" in args, proc.stdout
    _assert_token_confined(h, proc, FAKE_TOKEN)
    assert f"-n cat -- {token_file}" in _sudo_calls(h)


@ROOT_ONLY
def test_readable_config_unreadable_token_file_reads_token_through_sudo_n(tmp_path):
    h = _harness(tmp_path)
    token_file = _root_only(tmp_path / "cloudflare-purge-token", f"{FAKE_TOKEN}\n")
    h["conf"].write_text(f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE="{token_file}"\n')
    proc = _run_purge(h, _allow(token_file))
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "CDN cache purged" in proc.stdout, proc.stdout
    _assert_token_confined(h, proc, FAKE_TOKEN)
    assert _sudo_calls(h) == [f"-n cat -- {token_file}"], _sudo_calls(h)


@ROOT_ONLY
def test_sudo_refused_for_config_skips_nonfatal_without_curl(tmp_path):
    h = _harness(tmp_path)
    _root_only(h["conf"], f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN="{FAKE_TOKEN}"\n')
    proc = _run_purge(h)  # the stub refuses, as sudo -n does without a credential
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert f"{h['conf']} not readable by" in proc.stdout, proc.stdout
    assert "sudo -n could not read it" in proc.stdout, proc.stdout
    assert args == "" and stdin == "", "curl was called although sudo -n was refused"
    assert _sudo_calls(h) == ["-n true"], _sudo_calls(h)


@ROOT_ONLY
@pytest.mark.parametrize(
    "sudo_env",
    [
        # sudoers permits `true` but not grep: sudo exits 1, as grep does on no match
        pytest.param({"STUB_SUDO_MODE": "allow-true-only"}, id="sudo-refuses-grep"),
        # sudo runs grep but grep cannot open the file: grep exits 2
        pytest.param({"STUB_SUDO_MODE": "allow"}, id="privileged-grep-read-error"),
    ],
)
def test_privileged_read_failure_is_reported_not_mistaken_for_unset(tmp_path, sudo_env):
    """The sudo -n probe passes but the privileged read fails → config named, never "not set"."""
    h = _harness(tmp_path)
    _root_only(h["conf"], f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN="{FAKE_TOKEN}"\n')
    proc = _run_purge(h, sudo_env)  # STUB_SUDO_UNLOCK unset: the file stays mode 000
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert f"{h['conf']} not readable by" in proc.stdout, proc.stdout
    assert "sudo -n could not read it" in proc.stdout, proc.stdout
    assert "not set" not in proc.stdout, proc.stdout
    assert args == "" and stdin == "", "curl was called although the config was not read"
    calls = _sudo_calls(h)
    assert calls[0] == "-n true" and len(calls) == 2, calls  # one failed read, then no retries


def test_config_read_error_is_reported_not_mistaken_for_unset(tmp_path):
    """A config path that passes -r but cannot be read as a file (a directory)."""
    h = _harness(tmp_path)
    h["conf"].mkdir()
    proc = _run_purge(h, {"STUB_SUDO_MODE": "allow"})
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert f"{h['conf']} could not be read by" in proc.stdout, proc.stdout
    assert "not set" not in proc.stdout, proc.stdout
    assert args == ""
    assert _sudo_calls(h) == [], _sudo_calls(h)


@ROOT_ONLY
def test_sudo_refused_for_token_file_skips_nonfatal_without_curl(tmp_path):
    h = _harness(tmp_path)
    token_file = _root_only(tmp_path / "cloudflare-purge-token", f"{FAKE_TOKEN}\n")
    h["conf"].write_text(f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE="{token_file}"\n')
    proc = _run_purge(h)
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert f"CLOUDFLARE_PURGE_TOKEN_FILE {token_file} not readable by" in proc.stdout
    assert "sudo -n could not read it" in proc.stdout, proc.stdout
    assert args == ""


@ROOT_ONLY
def test_environment_keys_win_and_need_no_privilege(tmp_path):
    """Both keys in the environment → the unreadable config is never opened via sudo."""
    h = _harness(tmp_path)
    _root_only(h["conf"], 'CF_ZONE_ID="confzone"\nCLOUDFLARE_PURGE_TOKEN="fake-conf-token"\n')
    proc = _run_purge(
        h,
        {"CF_ZONE_ID": "envzone", "CLOUDFLARE_PURGE_TOKEN": FAKE_TOKEN, **_allow(h["conf"])},
    )
    args, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "/zones/envzone/purge_cache" in args
    assert f"Bearer {FAKE_TOKEN}" in stdin and "fake-conf-token" not in stdin
    assert _sudo_calls(h) == [], (
        f"sudo was invoked with the keys in the environment: {_sudo_calls(h)}"
    )


@ROOT_ONLY
def test_env_token_file_still_reads_unreadable_config_for_inline_token(tmp_path):
    """
    CF_ZONE_ID + CLOUDFLARE_PURGE_TOKEN_FILE in the environment do NOT make the
    config irrelevant: an inline CLOUDFLARE_PURGE_TOKEN there beats the file, so
    the unreadable config is still read through sudo -n (docs/ARCHITECTURE.md,
    etc/audiobooks.conf.example state exactly when no privilege is used).
    """
    h = _harness(tmp_path)
    token_file = tmp_path / "cloudflare-purge-token"
    token_file.write_text("fake-file-token-obtt\n")
    _root_only(h["conf"], f'CLOUDFLARE_PURGE_TOKEN="{FAKE_TOKEN}"\n')
    proc = _run_purge(
        h,
        {
            "CF_ZONE_ID": "envzone",
            "CLOUDFLARE_PURGE_TOKEN_FILE": str(token_file),
            **_allow(h["conf"]),
        },
    )
    _, stdin = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "fake-file-token-obtt" not in stdin, "the file token beat the config's inline token"
    _assert_token_confined(h, proc, FAKE_TOKEN)
    assert _sudo_calls(h)[0] == "-n true", _sudo_calls(h)


@ROOT_ONLY
def test_environment_zone_wins_over_privileged_config_zone(tmp_path):
    """Zone in the environment, token only in the unreadable config → env zone, conf token."""
    h = _harness(tmp_path)
    _root_only(h["conf"], f'CF_ZONE_ID="confzone"\nCLOUDFLARE_PURGE_TOKEN="{FAKE_TOKEN}"\n')
    proc = _run_purge(h, {"CF_ZONE_ID": "envzone", **_allow(h["conf"])})
    args, _ = _curl_seen(h)
    assert "rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert "/zones/envzone/purge_cache" in args and "confzone" not in args
    _assert_token_confined(h, proc, FAKE_TOKEN)


def test_readable_config_and_token_file_never_invoke_privilege(tmp_path):
    h = _harness(tmp_path)
    token_file = tmp_path / "cloudflare-purge-token"
    token_file.write_text(f"{FAKE_TOKEN}\n")
    h["conf"].write_text(f'CF_ZONE_ID="abc123"\nCLOUDFLARE_PURGE_TOKEN_FILE="{token_file}"\n')
    proc = _run_purge(h, {"STUB_SUDO_MODE": "allow"})
    assert "CDN cache purged" in proc.stdout, proc.stdout + proc.stderr
    assert _sudo_calls(h) == [], f"sudo was invoked for readable files: {_sudo_calls(h)}"


def test_privileged_reads_are_non_interactive_in_upgrade_sh():
    """The seam runs `command sudo -n` — bypassing the prompting sudo() wrapper."""
    body = _extract("_purge_privileged", UPGRADE_SH.read_text())
    assert body, "_purge_privileged() not found in upgrade.sh"
    assert "command sudo -n " in _code_only(body)


def test_legacy_install_dir_check_recognises_compat_symlink():
    """
    /usr/local/lib/audiobooks is INTENTIONALLY created as a backward-compat
    symlink → ${APP_DIR}/lib by install.sh. The audit_and_cleanup section
    must distinguish between a real legacy directory (warn + remove) and
    the expected compat symlink (no-op).
    """
    text = UPGRADE_SH.read_text()
    # Must use -L (symlink test) before -d (directory test)
    assert '[[ -L "/usr/local/lib/audiobooks" ]]' in text, (
        "audit_and_cleanup must use -L test for /usr/local/lib/audiobooks "
        "before treating it as a legacy real directory"
    )
    # Must verify the symlink points to the canonical lib location
    assert "readlink -f /usr/local/lib/audiobooks" in text, (
        "audit_and_cleanup must verify the symlink resolves to the canonical "
        "lib location before declaring it healthy"
    )
