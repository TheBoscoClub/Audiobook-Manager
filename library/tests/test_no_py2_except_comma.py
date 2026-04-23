"""Guard against regression of the Py2-style `except A, B:` pattern.

Python 3 silently parses `except ValueError, TypeError:` as
`except ValueError as TypeError:` — it binds the exception object to a
local variable named `TypeError`, shadowing the built-in. The `TypeError`
exception class is NOT also caught. This pattern looks like multi-exception
handling but silently swallows only the first class; any real `TypeError`
raised inside the block propagates unhandled.

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
EXCEPT_COMMA_RE = re.compile(
    rf"^\s*except\s+{_ID}(?:\s*,\s*{_ID})+\s*:"
)


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
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if EXCEPT_COMMA_RE.match(line):
                rel = path.relative_to(PROJECT_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Found Py2-style `except A, B:` patterns — these silently swallow "
        "only the FIRST exception (Python 3 parses `except A, B:` as "
        "`except A as B:`). Convert to `except (A, B):`:\n"
        + "\n".join(offenders)
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
        assert not EXCEPT_COMMA_RE.match(line), (
            f"regex falsely matched valid line: {line!r}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
