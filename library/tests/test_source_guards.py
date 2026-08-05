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
