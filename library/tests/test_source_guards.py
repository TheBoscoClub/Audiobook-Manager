"""Source-scanning regression guards for patterns no linter will catch here.

Sibling of ``test_no_py2_except_comma.py``: each test below scans the tree for
a pattern that a project-level configuration decision has made invisible to
the normal tooling, so the tooling cannot be the thing that catches it.

1. **Interpolated SQL** — `.bandit` carries a GLOBAL skip for B608
   (hardcoded_sql_expressions). The skip is justified for the 22 audited
   sites (bandit reports on the SQL-literal line rather than the
   `conn.execute(` line, so per-site `# nosec` comments do not cover the
   reported line), but a global skip means a NEW f-string SQL site — one that
   interpolates a user-controlled value straight into a query — can never be
   flagged again. This guard restores the signal: every interpolated SQL
   string must carry the audited `# nosec B608` annotation, so writing a new
   one is a deliberate, reviewable act rather than an invisible default.

2. **Committed TOTP seeds** — three functional seeds for the test VM's
   `testadmin` account were committed as default values in
   `library/tests/`. This repository is public. See
   `tests/helpers/vm_credentials.py`.

3. **Bare `rglob` tree walks** — file discovery over the Library tree used to
   be re-implemented at nine call sites, each carrying its own idea of what to
   include. That is a by-vigilance pattern, and it failed twice in production:
   Audiobook-Manager-94p (one collector missed the `translated/`
   chapter-artifact exclusion, inflating the Conversion Progress card from
   1,867 books to 5,829) and Audiobook-Manager-2sw (the same artifacts
   surfacing in the grouped-library API). Fixing each site individually left
   the class alive — the tenth collector would reproduce it. The rules now
   live in `scanner/utils/canonical.py`, whose iterators apply them by
   default; this guard makes writing a new bare `rglob` a deliberate,
   reviewable act (add an allowlist entry with a reason) instead of the path
   of least resistance. See Audiobook-Manager-6cx (which introduced the
   iterator) and Audiobook-Manager-fud (the remaining call sites, and this
   guard).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS = ("library", "scripts")
EXCLUDE_DIRS = {
    "venv",
    ".snapshots",
    ".btrbk-snapshots",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "_archive",
}

# ── 1. Interpolated SQL ────────────────────────────────────────────────

# A SQL string starts with a statement verb, or is a continuation fragment
# appended to one (`sql += f" WHERE name = {x}"`). Anchoring at the start is
# what keeps prose out: `f"Failed to insert into {table}"` is an error
# message, not a query.
SQL_STATEMENT_RE = re.compile(
    r"^\s*(SELECT\s|INSERT\s+INTO\s|INSERT\s+OR\s|REPLACE\s+INTO\s|UPDATE\s"
    r"|DELETE\s+FROM\s|CREATE\s+TABLE\s|CREATE\s+INDEX\s|ALTER\s+TABLE\s"
    r"|DROP\s+TABLE\s|DROP\s+INDEX\s"
    r"|WHERE\s|ORDER\s+BY\s|GROUP\s+BY\s|LIMIT\s|VALUES\s"
    r"|LEFT\s+JOIN\s|INNER\s+JOIN\s|JOIN\s)",
    re.IGNORECASE,
)
# Deliberately NOT in the list: bare `SET`, `AND`, `OR`, `PRAGMA`. Each is a
# common English word or shell token at the start of a string (`set -e`,
# "PRAGMA table_info on unlisted table: {t}"), and B608 does not cover PRAGMA
# at all. The cost is that a bare `sql += f" AND x = {v}"` fragment is not
# detected; every such fragment in this tree is a plain string with `?`
# placeholders, and the statement it is appended to IS detected.
NOSEC_B608_RE = re.compile(r"#\s*nosec\b[^#\n]*\bB608\b")


def _iter_python_files(include_tests: bool) -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if not include_tests and "tests" in path.parts:
                continue
            files.append(path)
    return sorted(files)


def _static_text(node: ast.JoinedStr) -> str:
    """Concatenate the literal (non-interpolated) parts of an f-string."""
    return "".join(
        part.value
        for part in node.values
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    )


def _interpolated_sql_sites(path: Path) -> list[tuple[int, int]]:
    """Return (start_line, end_line) for every f-string that builds SQL."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):  # fmt: skip
        return []
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        if not any(isinstance(part, ast.FormattedValue) for part in node.values):
            continue  # no interpolation — not a construction site
        if SQL_STATEMENT_RE.search(_static_text(node)):
            sites.append((node.lineno, node.end_lineno or node.lineno))
    return sites


def _site_is_annotated(lines: list[str], start: int, end: int) -> bool:
    """True if `# nosec ... B608` covers the string literal.

    Two conventions are in use and both are accepted: the annotation on the
    f-string's own line (scanner/utils/db_helpers.py) and on the enclosing
    `conn.execute(` line immediately above it (auth/database.py). The window
    stops there — deliberately narrow, so an annotation on an unrelated
    nearby statement cannot launder a new site.
    """
    window = lines[max(0, start - 2) : end]
    return any(NOSEC_B608_RE.search(line) for line in window)


def test_every_interpolated_sql_site_is_annotated():
    """B608 is globally skipped — the annotation is the only remaining marker."""
    unannotated: list[str] = []
    total = 0
    for path in _iter_python_files(include_tests=True):
        lines = path.read_text(encoding="utf-8").splitlines()
        for start, end in _interpolated_sql_sites(path):
            total += 1
            if not _site_is_annotated(lines, start, end):
                rel = path.relative_to(PROJECT_ROOT)
                unannotated.append(f"{rel}:{start}: {lines[start - 1].strip()[:110]}")

    assert total > 0, "scanner found no interpolated SQL at all — the guard is broken"
    assert not unannotated, (
        "Interpolated SQL without a `# nosec B608` annotation. `.bandit` skips "
        "B608 globally, so bandit cannot flag these — the annotation is the "
        "audit trail. Before adding one, confirm every user-controlled value "
        "is bound through a `?` placeholder and only internal constants or "
        "allowlisted identifiers are interpolated:\n" + "\n".join(unannotated)
    )


def test_sql_guard_detects_a_new_unannotated_site(tmp_path):
    """Meta-test: the scanner must actually fire on a fresh violation."""
    sample = tmp_path / "sample.py"
    sample.write_text('def q(t, v):\n    return f"SELECT * FROM {t} WHERE name = {v}"\n')
    sites = _interpolated_sql_sites(sample)
    assert sites, "scanner missed an obvious interpolated SELECT"
    lines = sample.read_text().splitlines()
    start, end = sites[0]
    assert not _site_is_annotated(lines, start, end)


def test_sql_guard_ignores_parameterized_and_plain_strings(tmp_path):
    """Meta-test: no false positives on the safe forms."""
    sample = tmp_path / "safe.py"
    sample.write_text(
        "def q(c, v):\n"
        '    c.execute("SELECT * FROM books WHERE id = ?", (v,))\n'
        '    msg = f"loaded {v} rows"\n'
        "    return msg\n"
    )
    assert _interpolated_sql_sites(sample) == []


# ── 2. Committed TOTP seeds ────────────────────────────────────────────

# A base32 TOTP seed as pyotp emits it: 32 chars of A-Z2-7, quoted.
TOTP_SEED_RE = re.compile(r"""['"][A-Z2-7]{32}['"]""")


def test_no_hardcoded_totp_seeds_in_the_tree():
    """A functional TOTP seed in a public repo is a committed credential.

    Tests that need one read ``ADMIN_TOTP_SECRET`` from the environment
    (tests/helpers/vm_credentials.py); tests that create their own user should
    mint a throwaway with ``pyotp.random_base32()``.
    """
    offenders: list[str] = []
    for path in _iter_python_files(include_tests=True):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if TOTP_SEED_RE.search(line):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        "Base32 TOTP-seed literals found in the source tree. This repository "
        "is public; a working seed here is a credential leak:\n" + "\n".join(offenders)
    )


def test_totp_seed_guard_detects_a_seed():
    """Meta-test: the pattern must match a real pyotp.random_base32() output.

    The sample is assembled at runtime — writing a literal seed here would
    make this guard file its own first offender.
    """
    import pyotp

    sample = "SECRET = " + chr(34) + pyotp.random_base32() + chr(34)
    assert TOTP_SEED_RE.search(sample), sample
    assert not TOTP_SEED_RE.search("SECRET = " + chr(34) + "short" + chr(34))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── 3. Bare rglob tree walks ───────────────────────────────────────────

# Every `<expr>.rglob(...)` in non-test source, keyed by
# (path relative to PROJECT_ROOT, the receiver expression as source text),
# mapped to how many times it legitimately appears. Counting — rather than
# just listing the file — is what stops a NEW walk from hiding inside a file
# that already has a sanctioned one.
#
# Adding an entry here is the escape hatch, and it is meant to be used when
# the walk genuinely is not a Library-tree collection. Justify each one.
ALLOWED_RGLOB_SITES: dict[tuple[str, str], int] = {
    # The canonical iterators themselves. `iter_library_files` is the single
    # definition of the Library walk (cover-art + `translated/` exclusions on
    # by default); `iter_source_files` is the single definition of the
    # Sources walk. This is the home the other sites were migrated to.
    ("library/scanner/utils/canonical.py", "library_dir"): 1,
    ("library/scanner/utils/canonical.py", "sources_dir"): 1,
    # Conversion staging (AUDIOBOOKS_STAGING), not the Library. The task
    # deletes *every* leftover file and then reaps the emptied directories,
    # so "walk everything, exclude nothing" is the specification rather than
    # an oversight — routing it through the canonical iterator would invite
    # someone to switch the exclusions on and silently strand files there.
    # Two calls: one over files, one over directories in reverse order.
    ("library/backend/api_modular/maintenance_tasks/cleanup.py", "staging"): 2,
    # A per-import temporary directory holding an unpacked Google Play ZIP
    # (`tempfile.mkdtemp(prefix="gplay_")`), walked before anything has been
    # ingested. Not a library root, and its pattern list is deliberately
    # different — case variants and `.aac`, matching what that vendor ships.
    ("library/scripts/google_play_processor.py", "directory"): 1,
}


def _rglob_sites(path: Path) -> list[tuple[str, int]]:
    """Return (receiver-expression, lineno) for every `X.rglob(...)` call."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):  # fmt: skip
        return []
    return [
        (ast.unparse(node.func.value), node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rglob"
    ]


def _collect_rglob_sites() -> dict[tuple[str, str], list[int]]:
    sites: dict[tuple[str, str], list[int]] = {}
    for path in _iter_python_files(include_tests=False):
        rel = str(path.relative_to(PROJECT_ROOT))
        for receiver, lineno in _rglob_sites(path):
            sites.setdefault((rel, receiver), []).append(lineno)
    return sites


def test_no_unallowlisted_rglob_outside_the_canonical_iterator():
    """A new tree walk must go through `scanner.utils.canonical`.

    The canonical iterators apply the Library exclusions by default, so a
    collector written against them is correct without its author having to
    know the rules. A bare `rglob` opts out of that silently — this makes it
    fail loudly instead.
    """
    sites = _collect_rglob_sites()
    assert sites, "scanner found no rglob calls at all — the guard is broken"

    offenders: list[str] = []
    for key, linenos in sorted(sites.items()):
        rel, receiver = key
        allowed = ALLOWED_RGLOB_SITES.get(key, 0)
        if len(linenos) > allowed:
            where = ", ".join(f"line {n}" for n in linenos)
            offenders.append(
                f"{rel}: {receiver}.rglob(...) x{len(linenos)} ({where}) — allowed {allowed}"
            )

    assert not offenders, (
        "Bare rglob tree walk(s) outside scanner/utils/canonical.py. Use "
        "`iter_library_files` / `iter_canonical_audiobook_files` for the "
        "Library tree or `iter_source_files` for Sources — they carry the "
        "cover-art and `translated/` exclusions that were re-derived (and "
        "once forgotten) at every call site. If this walk really is over a "
        "different tree, add it to ALLOWED_RGLOB_SITES with a reason:\n" + "\n".join(offenders)
    )


def test_rglob_allowlist_has_no_stale_entries():
    """An allowlist entry that no longer matches real code is dead weight.

    Without this, deleting a walk leaves its exemption behind, and the
    exemption silently pre-authorises the next one written in the same file
    against the same variable name.
    """
    sites = _collect_rglob_sites()
    stale = [
        f"{rel}: {receiver} (allowlisted {count}, found {len(sites.get((rel, receiver), []))})"
        for (rel, receiver), count in sorted(ALLOWED_RGLOB_SITES.items())
        if len(sites.get((rel, receiver), [])) != count
    ]
    assert not stale, "Stale ALLOWED_RGLOB_SITES entries — remove them:\n" + "\n".join(stale)


def test_rglob_guard_detects_a_new_bare_walk(tmp_path):
    """Meta-test: the detector must actually fire on a fresh violation."""
    sample = tmp_path / "collector.py"
    sample.write_text(
        'def collect(library_root):\n    return [p for p in library_root.rglob("*.opus")]\n'
    )
    sites = _rglob_sites(sample)
    assert sites == [("library_root", 2)], sites
    assert ("collector.py", "library_root") not in ALLOWED_RGLOB_SITES


def test_rglob_guard_ignores_the_canonical_iterator_calls(tmp_path):
    """Meta-test: no false positives on the sanctioned form."""
    sample = tmp_path / "consumer.py"
    sample.write_text(
        "from scanner.utils.canonical import iter_library_files\n"
        "def collect(root):\n"
        '    return list(iter_library_files(root, ("*.opus",)))\n'
    )
    assert _rglob_sites(sample) == []


# ── 4. Unconditional STARTTLS on a mail send ───────────────────────────
#
# Since the relay migration (Audiobook-Manager-9nu) the project submits to
# 127.0.0.1:25 with no credential. That relay does NOT advertise STARTTLS, so a
# bare `server.starttls()` raises SMTPNotSupportedError. Every send site wraps
# itself in a broad `except Exception` that logs only the exception CLASS and
# returns False, and at least one caller (auth.py:881) discards that False — so
# the failure is invisible end to end and the user is shown success.
#
# Three sites shipped that way and went unnoticed through a release
# (auth_email.py `_send_admin_alert` and `_send_reply_email`, inbox_cli.py's
# operator reply): admin contact alerts, admin replies, and `audiobook-inbox
# reply` all silently sent nothing. They were found by an audit, not by a test,
# and the earlier verification that "missed" them counted `starttls()` calls
# against guard occurrences and got equal totals — equal counts prove nothing
# about PAIRING. This guard checks pairing.
#
# The rule: starttls() must sit INSIDE `if smtp_user and smtp_pass:` (or the
# `user and password` spelling), because TLS+AUTH only make sense when there is
# a credential to protect.

_MAIL_SEND_FILES = (
    "library/backend/api_modular/auth_email.py",
    "library/auth/inbox_cli.py",
    "library/auth/audit.py",
    "library/translation_monitor/notify.py",
    "scripts/email-report.py",
)

_CREDENTIAL_GUARD = re.compile(r"if\s+(smtp_user\s+and\s+smtp_pass|user\s+and\s+password)\s*:")


def _unguarded_starttls(text: str) -> list[int]:
    """Return 1-indexed line numbers of starttls() calls with no credential guard.

    A guard counts when it appears within the preceding 5 lines and is indented
    LESS than the starttls() call — i.e. starttls() is inside its block.
    """
    lines = text.splitlines()
    bad: list[int] = []
    for i, line in enumerate(lines):
        # Strip trailing comments before looking for the call. The fix for the
        # original bug DOCUMENTS itself with the word starttls() in a comment,
        # and an earlier cut of this guard flagged its own explanation as an
        # offence. (A "#" inside a string literal would also cut here; no mail
        # sender has one, and a false NEGATIVE is impossible — only a missed
        # detection on a line that is already suspicious enough to inspect.)
        code = line.split("#", 1)[0]
        if "starttls()" not in code:
            continue
        indent = len(line) - len(line.lstrip())
        guarded = False
        for j in range(max(0, i - 5), i):
            prev = lines[j]
            if _CREDENTIAL_GUARD.search(prev) and (len(prev) - len(prev.lstrip())) < indent:
                guarded = True
                break
        if not guarded:
            bad.append(i + 1)
    return bad


def test_no_unconditional_starttls_on_any_mail_send():
    offenders = []
    for rel in _MAIL_SEND_FILES:
        path = PROJECT_ROOT / rel
        if not path.is_file():
            continue
        for lineno in _unguarded_starttls(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "starttls() outside `if smtp_user and smtp_pass:` — this RAISES against the "
        "credential-less local relay and the surrounding `except Exception` swallows "
        f"it, so the send fails silently: {offenders}"
    )


def test_starttls_guard_detects_an_unguarded_call(tmp_path):
    sample = tmp_path / "sender.py"
    sample.write_text(
        "with smtplib.SMTP(h, p) as server:\n"
        "    server.starttls()\n"
        "    if smtp_user and smtp_pass:\n"
        "        server.login(smtp_user, smtp_pass)\n",
        encoding="utf-8",
    )
    assert _unguarded_starttls(sample.read_text()) == [2]


def test_starttls_guard_accepts_a_properly_guarded_call(tmp_path):
    sample = tmp_path / "sender.py"
    sample.write_text(
        "with smtplib.SMTP(h, p) as server:\n"
        "    if smtp_user and smtp_pass:\n"
        "        server.starttls()\n"
        "        server.login(smtp_user, smtp_pass)\n",
        encoding="utf-8",
    )
    assert _unguarded_starttls(sample.read_text()) == []


# ── 5. Operator-intent guard on unit enablement ────────────────────────
#
# upgrade.sh force-enabled units from three independent paths and consulted no
# state, so `systemctl disable` survived only until the next upgrade. The v8.4.2.5
# deploy silently re-enabled and restarted all three translation units, reverting
# a deliberate operator mitigation (Audiobook-Manager-6ap).
#
# The fix records intent in ${CONFIG_DIR}/disabled-units and honours it in
# _enable_unit_smart. install.sh carries a deliberately duplicated copy of that
# helper (it bootstraps before any shared shell library exists on the target),
# and the two are documented as MUST stay in sync — so drift is the failure mode
# these tests exist to catch.
#
# The process-substitution assertion is not stylistic. The first cut used
# `grep -qxF "$unit" <(sed ... )`, which needs /dev/fd; where that is
# unavailable the test simply evaluates false and the guard NEVER FIRES while
# looking correct. Measured: the procsub form failed to match a list entry the
# pipe form matched byte-identically.

_ENABLE_HELPER_FILES = ("upgrade.sh", "install.sh")


def _enable_helper_bodies() -> dict[str, str]:
    bodies: dict[str, str] = {}
    for rel in _ENABLE_HELPER_FILES:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        idx = text.find("_enable_unit_smart()")
        assert idx != -1, f"{rel} no longer defines _enable_unit_smart"
        bodies[rel] = text[idx : idx + 4000]
    return bodies


def test_both_enable_helpers_honour_operator_intent():
    for rel, body in _enable_helper_bodies().items():
        assert "disabled-units" in body, (
            f"{rel}::_enable_unit_smart does not consult the operator-intent list — "
            "an upgrade would silently re-enable units the operator turned off"
        )


def test_operator_intent_uses_a_drop_in_condition_not_disable_or_mask():
    """`disable` and `mask` were both tried against the real system and BOTH failed.

    disable — only removes the boot-time WantedBy symlink. audiobook.target
      declares the translation units with `Wants=` and upgrade.sh starts that
      target, so a *disabled* unit is still STARTED. Measured on the v8.4.3
      deploy: all three came back enabled=disabled active=active.
    mask — needs to symlink SYSTEMD_DIR/<unit> to /dev/null, but these units ARE
      real files at exactly that path, so the mask silently does nothing and
      exits 0. Checking only its exit status would have reported success twice.

    Only a drop-in condition survives a `Wants=` pull, so assert the mechanism
    rather than merely that the list is consulted — the weaker assertion is
    precisely what let the broken version through.
    """
    for rel, body in _enable_helper_bodies().items():
        code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
        assert "zz-operator-disabled" in code, (
            f"{rel}::_enable_unit_smart must write a drop-in for a listed unit; "
            "disable alone does not stop a target from starting it"
        )
        assert "ConditionPathExists" in code, (
            f"{rel}::_enable_unit_smart must gate the unit on an unsatisfiable "
            "condition, which systemd evaluates on every start attempt"
        )
        assert "systemctl mask" not in code, (
            f"{rel}::_enable_unit_smart must not rely on `systemctl mask` — it "
            "cannot mask a unit whose real file lives in the same directory, and "
            "it fails silently when it cannot"
        )


def test_enable_helpers_do_not_use_process_substitution_for_the_check():
    for rel, body in _enable_helper_bodies().items():
        window = body[: body.find("disabled-units") + 600]
        # Strip shell comments first: the fix DOCUMENTS itself by naming the
        # rejected `<(...)` form, and an earlier cut of this guard flagged its
        # own explanation. (Second time today a literal-minded guard caught its
        # own comment — that is the guard working, not misfiring.)
        code = "\n".join(ln.split("#", 1)[0] for ln in window.splitlines())
        assert "<(" not in code, (
            f"{rel}::_enable_unit_smart uses process substitution near the "
            "operator-intent check. That needs /dev/fd; where it is unavailable the "
            "condition evaluates false and the guard silently never fires. Use a pipe."
        )


# ---------------------------------------------------------------------------
# Sender identity (Audiobook-Manager-9nu)
#
# The relay selects its upstream credential by envelope sender. A default of
# `someone@localhost` is therefore not a harmless placeholder: local submission
# returns 250 OK, the upstream rejects at MAIL FROM with 530, and the message
# hard-bounces where the application cannot see it. Fourteen messages were lost
# that way on 2026-08-26, one path being login/OTP mail.
#
# The rule: no source file may supply a localhost address as a sender default.
# Resolve through common_utils.mail_identity.resolve_sender, which refuses.
# ---------------------------------------------------------------------------

_LOCALHOST_SENDER = re.compile(r"""["'][A-Za-z0-9._%+-]+@localhost[A-Za-z0-9.]*["']""")


def test_no_localhost_sender_defaults_in_the_tree():
    offenders = []
    for path in _iter_python_files(include_tests=False):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _LOCALHOST_SENDER.search(line):
                offenders.append(f"{path}:{i}: {line.strip()}")
    assert not offenders, (
        "localhost sender default(s) found — these hard-bounce upstream while "
        "reporting local success. Use common_utils.mail_identity.resolve_sender:\n"
        + "\n".join(offenders)
    )


def test_localhost_sender_guard_detects_a_new_offender(tmp_path):
    """The guard must be able to fail, or it proves nothing."""
    bad = tmp_path / "bad.py"
    bad.write_text('FROM = os.environ.get("SMTP_FROM", "noreply@localhost")\n')
    assert _LOCALHOST_SENDER.search(bad.read_text())
    good = tmp_path / "good.py"
    good.write_text('FROM = resolve_sender(os.environ.get("SMTP_FROM"))\n')
    assert not _LOCALHOST_SENDER.search(good.read_text())
