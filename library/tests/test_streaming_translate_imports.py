"""Regression guard for streaming_translate module import health and style.

Under Python 3.14, `except A, B:` (bare-tuple form) parses as an expression
and is bytecode-identical to `except (A, B):` — both catch A or B. So the
bare-tuple form in v8.3.0 was NOT a SyntaxError and did NOT block module
import; the /api/translate/* failures during the 8.3.1 prod demo had a
different root cause (orphan streaming worker + missing TTS wiring).

The bare-tuple form is still discouraged because it is visually ambiguous
with the Py2 exception-binding syntax (`except Exc, name:`) and inconsistent
with the parenthesized form used elsewhere in the codebase. These tests
enforce the parenthesized convention within this module and guard the
module's load path.

Scope: this file only. A repo-wide sweep of the other ~18 files using the
bare-tuple form is tracked as a follow-up (see v8.3.2 plan Task 19 notes).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path


def test_streaming_translate_module_imports() -> None:
    """Module must import under Python 3 and expose handle_seek."""
    mod = importlib.import_module("backend.api_modular.streaming_translate")
    assert hasattr(
        mod, "handle_seek"
    ), "streaming_translate module imported but handle_seek is missing"


def test_except_clauses_use_parenthesized_tuples() -> None:
    """No bare-tuple `except A, B:` form — require `except (A, B):` instead."""
    src_path = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "api_modular"
        / "streaming_translate.py"
    )
    src = src_path.read_text(encoding="utf-8")
    match = re.search(r"except\s+[A-Z]\w+\s*,\s*[A-Z]\w+\s*:", src)
    assert match is None, (
        f"Found bare-tuple except clause in streaming_translate.py: "
        f"{match.group(0)!r} — use parenthesized form `except (A, B):`"
    )
