"""Guard against the unparenthesised `except A, B:` pattern.

The reason changed with Python 3.14 and is worth stating precisely, because
the pattern is no longer what it once was:

- Python 3.0-3.13: `except ValueError, TypeError:` is a **SyntaxError**.
- Python 3.14 (PEP 758): it is **legal** and means `except (ValueError, TypeError):`.

So on this project's interpreter the line is correct and harmless — and that
is exactly what makes it dangerous. CI runs the suite on **3.12, 3.13 and
3.14** (.github/workflows/ci.yml), so a file carrying this form imports fine
locally and fails to parse on two of three matrix entries.

It gets written by accident rather than by hand: `ruff format` rewrites source
into syntax legal for its `target-version`, and with `target-version = "py314"`
it *strips the parentheses* from a correctly-written `except (A, B):`. That is
how this guard fired on 2026-08-27. The root fix is pinning ruff's
target-version to the oldest supported interpreter (pyproject.toml), and this
test is the backstop for when that pin is wrong again.

Historical note: prior to 3.14 the danger was different — `except A as B:`
semantics were feared, binding the exception to a name shadowing the second
class. That reading no longer applies, but forbidding the form still does.

The correct form is `except (ValueError, TypeError):` — a parenthesised
tuple explicitly naming all caught classes.

This test scans the source tree for the dangerous pattern and fails if
any site regresses. Comments and strings are excluded: a real `except …:`
line begins with only whitespace before the keyword.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SCAN_ROOTS = ("library", "scripts")
EXCLUDE_DIRS = {"venv", ".snapshots", "__pycache__", ".pytest_cache", ".ruff_cache"}

# Dotted identifier chain — first segment may be lowercase for module
# namespaces (e.g. `subprocess.TimeoutExpired`), later segments match either
# case. Each segment must start with a letter/underscore.
_ID = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
EXCEPT_COMMA_RE = re.compile(rf"^\s*except\s+{_ID}(?:\s*,\s*{_ID})+\s*:")


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            files.append(path)
    return files


def test_no_py2_except_comma_regression() -> None:
    """No Python file may have `except A, B:` outside parenthesized tuple form."""
    offenders: list[str] = []
    for path in _iter_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # fmt: skip
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if EXCEPT_COMMA_RE.match(line):
                rel = path.relative_to(PROJECT_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Found Py2-style `except A, B:` patterns — these silently swallow "
        "only the FIRST exception (Python 3 parses `except A, B:` as "
        "`except A as B:`). Convert to `except (A, B):`:\n" + "\n".join(offenders)
    )


def test_guard_scanner_catches_known_bad_pattern() -> None:
    """Meta-test: regex must match the dangerous pattern."""
    bad_lines = [
        "except ValueError, TypeError:",
        "    except ValueError, TypeError:",
        "        except OSError, subprocess.TimeoutExpired:",
        "except ValueError, TypeError, AttributeError:",
    ]
    for line in bad_lines:
        assert EXCEPT_COMMA_RE.match(line), f"regex failed to match: {line!r}"


def test_guard_scanner_allows_correct_pattern() -> None:
    """Meta-test: regex must NOT match the correct form."""
    good_lines = [
        "except (ValueError, TypeError):",
        "    except (ValueError, TypeError):",
        "except ValueError:",
        "except ValueError as exc:",
        "# except ValueError, TypeError: -- comment referencing Py2 bug",
        "            # except OSError, RuntimeError: (historical fix note)",
    ]
    for line in good_lines:
        assert not EXCEPT_COMMA_RE.match(line), f"regex falsely matched valid line: {line!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
