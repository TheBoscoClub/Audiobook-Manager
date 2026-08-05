"""
Tests for the post-insert hook self-registration pattern (Audiobook-Manager-6cx).

The hardcoded fan-out in ``add_new_audiobooks._run_post_insert_hooks`` was
replaced by ``scanner.post_insert`` — a registry that hooks join via the
``register_post_insert`` decorator at import time. The historical failure
mode this guards against: the modular ``utilities_ops`` refactor silently
dropped the hash-generation hook for ~3.5 months (Audiobook-Manager-f5e)
because adding/keeping a hook required remembering to edit the fan-out
function. These tests assert:

1. The registry contains exactly the expected hooks, in order.
2. Every registered hook fires on a synthetic insert.
3. A raising hook is non-fatal and does not stop later hooks.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch


EXPECTED_HOOK_LABELS = [
    "Enrichment",
    "Verification",
    "Translation queue",
    "Hash generation",
    "Original print year",
]


class TestRegistryContents:
    def test_expected_hooks_registered_in_order(self):
        """Each feature hook self-registers at import time; losing one
        (the f5e failure mode) makes this fail loudly."""
        from scanner.post_insert import registered_post_insert_hooks

        labels = [label for label, _ in registered_post_insert_hooks()]
        assert labels == EXPECTED_HOOK_LABELS

    def test_register_post_insert_appends_and_returns_hook(self):
        import scanner.post_insert as post_insert

        def my_hook(audiobook_id, db_path):
            pass

        before = post_insert.registered_post_insert_hooks()
        try:
            returned = post_insert.register_post_insert("Test hook")(my_hook)
            assert returned is my_hook
            after = post_insert.registered_post_insert_hooks()
            assert after[-1] == ("Test hook", my_hook)
            assert len(after) == len(before) + 1
        finally:
            post_insert._POST_INSERT_HOOKS.remove(("Test hook", my_hook))


class TestAllHooksFireOnSyntheticInsert:
    def test_every_registered_hook_fires(self, temp_dir, monkeypatch):
        """Synthetic insert through ``_insert_one_audiobook`` must dispatch
        every registered hook with (audiobook_id, db_path)."""
        import scanner.post_insert as post_insert
        from scanner.add_new_audiobooks import _insert_one_audiobook

        fired: dict[str, tuple[int, Path]] = {}

        def make_recorder(label):
            def recorder(audiobook_id, db_path):
                fired[label] = (audiobook_id, db_path)

            return recorder

        # Wrap the real registry entries with recorders — same labels, same
        # dispatch loop, no heavy feature side effects.
        monkeypatch.setattr(
            post_insert,
            "_POST_INSERT_HOOKS",
            [(label, make_recorder(label)) for label, _ in post_insert._POST_INSERT_HOOKS],
        )

        db_path = temp_dir / "test.db"
        audio_file = temp_dir / "book.opus"
        audio_file.write_bytes(b"content")
        conn = MagicMock()

        with (
            patch("scanner.add_new_audiobooks.get_file_metadata") as mock_metadata,
            patch("scanner.add_new_audiobooks.extract_cover_art", return_value=None),
            patch("scanner.add_new_audiobooks.insert_audiobook", return_value=42),
        ):
            mock_metadata.return_value = {
                "title": "Synthetic Book",
                "author": "Author",
                "file_path": str(audio_file),
                "duration_hours": 1.0,
                "format": "opus",
            }
            status, book_info = _insert_one_audiobook(
                audio_file, conn, temp_dir, temp_dir / "covers", db_path, False
            )

        assert status == "added"
        assert book_info is not None and book_info["id"] == 42
        assert set(fired) == set(EXPECTED_HOOK_LABELS), (
            f"Not every registered hook fired on insert — missing: "
            f"{set(EXPECTED_HOOK_LABELS) - set(fired)}"
        )
        for label, (book_id, hook_db_path) in fired.items():
            assert book_id == 42, f"{label} hook received wrong audiobook_id"
            assert hook_db_path == db_path, f"{label} hook received wrong db_path"


class TestHookFailureIsolation:
    def test_raising_hook_is_non_fatal_and_later_hooks_run(self, monkeypatch, capsys):
        import scanner.post_insert as post_insert

        fired = []

        def bad_hook(audiobook_id, db_path):
            raise RuntimeError("simulated hook failure")

        def good_hook(audiobook_id, db_path):
            fired.append(audiobook_id)

        monkeypatch.setattr(
            post_insert,
            "_POST_INSERT_HOOKS",
            [("Bad", bad_hook), ("Good", good_hook)],
        )

        # Must not raise
        post_insert.run_post_insert_hooks(7, Path("/tmp/unused.db"))  # noqa: S108 — path is never opened; hooks are stubs  # nosec B108

        assert fired == [7], "Hook after a failing hook did not run"
        captured = capsys.readouterr()
        assert "Bad error (non-fatal): simulated hook failure" in captured.err

    def test_falsy_audiobook_id_skips_all_hooks(self, monkeypatch):
        import scanner.post_insert as post_insert

        fired = []
        monkeypatch.setattr(
            post_insert,
            "_POST_INSERT_HOOKS",
            [("Any", lambda audiobook_id, db_path: fired.append(audiobook_id))],
        )

        post_insert.run_post_insert_hooks(0, Path("/tmp/unused.db"))  # noqa: S108 — path is never opened; hooks are stubs  # nosec B108
        assert fired == []
