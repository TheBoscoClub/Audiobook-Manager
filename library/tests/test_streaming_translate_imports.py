"""Regression guard for streaming_translate module import health.

Under Python 3.14, `except A, B:` (bare-tuple form) parses as an expression
and is bytecode-identical to `except (A, B):` — both catch A or B. So the
bare-tuple form in v8.3.0 was NOT a SyntaxError and did NOT block module
import; the /api/translate/* failures during the 8.3.1 prod demo had a
different root cause (orphan streaming worker + missing TTS wiring).

We used to also enforce the parenthesized form stylistically, but `ruff
format` now unwraps bare-name tuples in `except (A, B):` → `except A, B:`
(idiomatic Python 3). Fighting the formatter over a bytecode-identical
stylistic preference produces churn without safety benefit, so that guard
has been dropped. What IS still worth guarding is the truly dangerous Py2
**binding** form `except Exc, name:` where `name` is a lowercase binding,
not another exception class — that IS a SyntaxError in Python 3.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path


def test_streaming_translate_module_imports() -> None:
    """Module must import under Python 3 and expose handle_seek."""
    mod = importlib.import_module("backend.api_modular.streaming_translate")
    assert hasattr(mod, "handle_seek"), (
        "streaming_translate module imported but handle_seek is missing"
    )


def test_no_py2_except_binding_form() -> None:
    """No Py2 `except Exc, name:` binding form (lowercase RHS = SyntaxError in Py3).

    Bare-tuple form `except A, B:` (both uppercase) is allowed — it is
    bytecode-identical to `except (A, B):` and is what ruff-format produces.
    """
    src_path = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "api_modular"
        / "streaming_translate.py"
    )
    src = src_path.read_text(encoding="utf-8")
    # Match: `except <UpperExc>, <lowercase_name>:` — lowercase RHS is the
    # dangerous Py2 binding form. `except <UpperExc>, <UpperExc>:` (tuple
    # form) is skipped because that's the ruff-canonical bare-tuple form.
    match = re.search(r"except\s+[A-Z]\w+\s*,\s*[a-z]\w*\s*:", src)
    assert match is None, (
        f"Found Py2 except-binding form in streaming_translate.py: "
        f"{match.group(0)!r} — this is a SyntaxError in Python 3. "
        f"Use `except Exc as name:` instead."
    )
