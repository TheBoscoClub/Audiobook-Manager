"""Regression tests for the CodeQL group-D fixes (Audiobook-Manager-o9s).

Covers:
- py/polynomial-redos in ``suggestions.sanitize_message`` — the tag-strip regex
  must run in linear time on adversarial input (a run of ``<`` characters).
- py/flask-debug in ``api_server`` — the Werkzeug debugger may only be armed
  when the project's explicit dev switch AND FLASK_DEBUG are both set.

``api_server`` is deliberately not imported: importing it applies gevent
monkey-patching and exits when no database exists. The helpers under test are
pure functions, so they are lifted from the module source via ``ast`` instead
(the same source-level approach ``test_gunicorn_migration.py`` uses).
"""

import ast
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from backend.api_modular.suggestions import sanitize_message

_API_SERVER_SRC = Path(__file__).resolve().parent.parent / "backend" / "api_server.py"

# A run of "<" with no closing ">" is the worst case for a [^>]-class tag
# regex: every "<" rescans the remainder. The quadratic form took 5.2 s on
# this input; the linear form takes well under 10 ms. The bound is generous
# so a slow CI runner cannot flake it while still separating the two by an
# order of magnitude.
_ADVERSARIAL_LEN = 40_000
_TIME_BOUND_S = 1.0


def _timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


# ---------------------------------------------------------------------------
# py/polynomial-redos — suggestions.py
# ---------------------------------------------------------------------------


class TestSanitizeMessageLinearTime:
    def test_run_of_open_brackets_is_linear(self):
        result, elapsed = _timed(sanitize_message, "<" * _ADVERSARIAL_LEN)
        assert elapsed < _TIME_BOUND_S, (
            f"sanitize_message took {elapsed:.2f}s on {_ADVERSARIAL_LEN} '<'"
        )
        # Unclosed brackets are not tags; they survive as literal text.
        assert result.startswith("<")

    def test_open_brackets_with_trailing_close_is_linear(self):
        # "<<<<...<>" — the one ">" at the end is what makes [^>]*? scan to it
        # from every single "<".
        _, elapsed = _timed(sanitize_message, "<" * _ADVERSARIAL_LEN + ">")
        assert elapsed < _TIME_BOUND_S, f"sanitize_message took {elapsed:.2f}s"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("<b>bold</b> text", "bold text"),
            ('<script>alert("xss")</script>', 'alert("xss")'),
            ("<div><span></span></div>", ""),
            ("a < b and c > d", "a d"),
        ],
    )
    def test_tag_stripping_semantics_unchanged(self, raw, expected):
        assert sanitize_message(raw) == expected

    @pytest.fixture
    def suggestions_table(self, auth_app):
        """Create the user_suggestions table and point the blueprint at auth_app's DB.

        Mirrors ``test_suggestions.py::clean_suggestions``: the auth_app fixture's
        inline schema has no user_suggestions table, and another create_app()
        call may have re-pointed the blueprint's module-level _db_path.
        """
        db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")
        for mod_name in list(sys.modules):
            if mod_name.endswith("api_modular.suggestions"):
                mod = sys.modules[mod_name]
                if hasattr(mod, "init_suggestions_routes"):
                    mod.init_suggestions_routes(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("DELETE FROM user_suggestions")
        conn.commit()
        conn.close()
        yield
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM user_suggestions")
        conn.commit()
        conn.close()

    def test_post_adversarial_message_completes_quickly(self, user_client, suggestions_table):
        payload = {"message": "<" * _ADVERSARIAL_LEN}
        resp, elapsed = _timed(lambda: user_client.post("/api/suggestions", json=payload))
        assert elapsed < _TIME_BOUND_S, f"POST /api/suggestions took {elapsed:.2f}s"
        # The sanitized text is a run of "<" (literal, no tags), capped at
        # MAX_MESSAGE_LENGTH by the endpoint; it is accepted as ordinary text.
        assert resp.status_code in (200, 201), resp.get_json()


# ---------------------------------------------------------------------------
# py/flask-debug — api_server.py
# ---------------------------------------------------------------------------


def _load_debug_helpers():
    """Lift ``_env_flag`` and ``_debug_enabled`` out of api_server.py without importing it."""
    tree = ast.parse(_API_SERVER_SRC.read_text(), filename=str(_API_SERVER_SRC))
    wanted = {"_env_flag", "_debug_enabled"}
    nodes: list[ast.stmt] = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted
    ]
    assert {n.name for n in nodes if isinstance(n, ast.FunctionDef)} == wanted, (
        "api_server.py must define _env_flag and _debug_enabled"
    )
    namespace: dict = {}
    # exec of the project's OWN two helper functions, lifted from api_server.py so
    # the test does not import the module (import builds the Flask app). No
    # external input reaches this call.
    code = compile(ast.Module(body=nodes, type_ignores=[]), str(_API_SERVER_SRC), "exec")
    exec(code, namespace)  # noqa: S102  # nosec B102
    return namespace["_debug_enabled"]


class TestFlaskDebugGate:
    @pytest.fixture(scope="class")
    def debug_enabled(self):
        return _load_debug_helpers()

    def test_flask_debug_alone_does_not_arm_debugger(self, debug_enabled):
        # The attack: a production-shaped environment that happens to carry
        # FLASK_DEBUG. The project's own dev switch is absent, so no debugger.
        assert debug_enabled({"FLASK_DEBUG": "true"}) is False
        assert debug_enabled({"FLASK_DEBUG": "1", "AUDIOBOOKS_DEV_MODE": "false"}) is False

    def test_dev_mode_alone_does_not_arm_debugger(self, debug_enabled):
        assert debug_enabled({"AUDIOBOOKS_DEV_MODE": "true"}) is False

    def test_empty_environment_does_not_arm_debugger(self, debug_enabled):
        assert debug_enabled({}) is False

    def test_both_switches_arm_debugger(self, debug_enabled):
        assert debug_enabled({"AUDIOBOOKS_DEV_MODE": "true", "FLASK_DEBUG": "true"}) is True
        assert debug_enabled({"AUDIOBOOKS_DEV_MODE": "yes", "FLASK_DEBUG": "1"}) is True

    def test_main_block_uses_the_gate(self):
        src = _API_SERVER_SRC.read_text()
        assert "if _debug_enabled(os.environ):" in src
        assert 'os.environ.get("FLASK_DEBUG"' not in src, (
            "FLASK_DEBUG must only be read through _debug_enabled"
        )
