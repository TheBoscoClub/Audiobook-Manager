"""Tests for `library.common_utils.secret_resolver.resolve_secret`.

Covers the env-var vs `*_FILE` pointer precedence, file read errors, whitespace
trimming, and missing-credential default behavior. All tests are hermetic — they
use `tmp_path` for any filesystem state and `monkeypatch.setenv`/`delenv` for
env-var control, so they never touch the operator's real `/etc/audiobooks/`.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest
from common_utils.secret_resolver import resolve_secret


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    """Every test starts with a clean slate for the credentials under test."""
    for key in (
        "SMTP_PASS",
        "SMTP_PASS_FILE",
        "AUDIOBOOKS_DEEPL_API_KEY",
        "AUDIOBOOKS_DEEPL_API_KEY_FILE",
        "AUDIOBOOKS_RUNPOD_API_KEY",
        "AUDIOBOOKS_RUNPOD_API_KEY_FILE",
        "TEST_SECRET_RESOLVER",
        "TEST_SECRET_RESOLVER_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


# ─── 1. Env-var-only path ──────────────────────────────────────────────────


def test_env_var_only_returns_env_value(monkeypatch):
    monkeypatch.setenv("SMTP_PASS", "inline-secret-123")
    assert resolve_secret("SMTP_PASS") == "inline-secret-123"


def test_env_var_only_works_for_all_three_supported_credentials(monkeypatch):
    monkeypatch.setenv("AUDIOBOOKS_DEEPL_API_KEY", "deepl-abc")
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_API_KEY", "runpod-xyz")
    assert resolve_secret("AUDIOBOOKS_DEEPL_API_KEY") == "deepl-abc"
    assert resolve_secret("AUDIOBOOKS_RUNPOD_API_KEY") == "runpod-xyz"


# ─── 2. File-only path ─────────────────────────────────────────────────────


def test_file_only_returns_file_content(monkeypatch, tmp_path: Path):
    secret_file = tmp_path / "smtp-pass"
    secret_file.write_text("file-secret-abc")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    assert resolve_secret("SMTP_PASS") == "file-secret-abc"


def test_file_only_works_for_deepl_runpod(monkeypatch, tmp_path: Path):
    deepl_file = tmp_path / "deepl-key"
    runpod_file = tmp_path / "runpod-key"
    deepl_file.write_text("deepl-from-file")
    runpod_file.write_text("runpod-from-file")
    monkeypatch.setenv("AUDIOBOOKS_DEEPL_API_KEY_FILE", str(deepl_file))
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_API_KEY_FILE", str(runpod_file))
    assert resolve_secret("AUDIOBOOKS_DEEPL_API_KEY") == "deepl-from-file"
    assert resolve_secret("AUDIOBOOKS_RUNPOD_API_KEY") == "runpod-from-file"


# ─── 3. Both set → env var wins ────────────────────────────────────────────


def test_env_var_wins_over_file_when_both_set(monkeypatch, tmp_path: Path):
    secret_file = tmp_path / "smtp-pass"
    secret_file.write_text("file-loses")
    monkeypatch.setenv("SMTP_PASS", "env-wins")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    assert resolve_secret("SMTP_PASS") == "env-wins"


def test_empty_env_var_falls_through_to_file(monkeypatch, tmp_path: Path):
    """An empty env var should not block the *_FILE fallback — empty is unset."""
    secret_file = tmp_path / "smtp-pass"
    secret_file.write_text("from-file")
    monkeypatch.setenv("SMTP_PASS", "")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    assert resolve_secret("SMTP_PASS") == "from-file"


def test_whitespace_only_env_var_falls_through_to_file(monkeypatch, tmp_path: Path):
    """An all-whitespace env var should fall through to *_FILE."""
    secret_file = tmp_path / "smtp-pass"
    secret_file.write_text("from-file")
    monkeypatch.setenv("SMTP_PASS", "   \t  \n  ")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    assert resolve_secret("SMTP_PASS") == "from-file"


# ─── 4. Neither set → returns default ──────────────────────────────────────


def test_neither_set_returns_empty_default():
    assert resolve_secret("SMTP_PASS") == ""


def test_neither_set_returns_explicit_default():
    assert resolve_secret("SMTP_PASS", default="my-default") == "my-default"


def test_unrelated_env_var_does_not_leak(monkeypatch):
    """resolve_secret('SMTP_PASS') should not pick up SOME_OTHER_VAR."""
    monkeypatch.setenv("SOME_OTHER_VAR", "should-not-appear")
    assert resolve_secret("SMTP_PASS") == ""


# ─── 5. File path set but file missing → default + warning ────────────────


def test_missing_file_returns_default_and_warns(monkeypatch, tmp_path: Path, caplog):
    monkeypatch.setenv("SMTP_PASS_FILE", str(tmp_path / "does-not-exist"))
    with caplog.at_level(logging.WARNING):
        result = resolve_secret("SMTP_PASS", default="fallback")
    assert result == "fallback"
    assert any("does not exist" in r.message for r in caplog.records)


# ─── 6. File path set but file empty → default + warning ──────────────────


def test_empty_file_returns_default_and_warns(monkeypatch, tmp_path: Path, caplog):
    secret_file = tmp_path / "empty-secret"
    secret_file.write_text("")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    with caplog.at_level(logging.WARNING):
        result = resolve_secret("SMTP_PASS", default="fallback")
    assert result == "fallback"
    assert any("is empty" in r.message for r in caplog.records)


def test_whitespace_only_file_returns_default(monkeypatch, tmp_path: Path):
    """A file containing only whitespace is treated as empty."""
    secret_file = tmp_path / "ws-secret"
    secret_file.write_text("   \n\t  \n")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    assert resolve_secret("SMTP_PASS", default="fallback") == "fallback"


# ─── 7. File unreadable → default + warning ───────────────────────────────


def test_unreadable_file_returns_default_and_warns(
    monkeypatch, tmp_path: Path, caplog
):
    """A 000-mode file triggers PermissionError on read; resolve_secret swallows it."""
    if os.geteuid() == 0:
        pytest.skip("PermissionError cannot be exercised when running as root")
    secret_file = tmp_path / "unreadable"
    secret_file.write_text("super-secret")
    secret_file.chmod(0o000)
    try:
        monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
        with caplog.at_level(logging.WARNING):
            result = resolve_secret("SMTP_PASS", default="fallback")
        assert result == "fallback"
        # Permission errors are recognized — the warning mentions perms or "error reading".
        assert any(
            "permission" in r.message.lower() or "error reading" in r.message.lower()
            for r in caplog.records
        )
    finally:
        # Restore mode so tmp_path cleanup works.
        secret_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_directory_path_returns_default_and_warns(monkeypatch, tmp_path: Path, caplog):
    """Pointing *_FILE at a directory raises IsADirectoryError (OSError subclass)
    — the generic OSError handler should catch it and fall back to default."""
    directory = tmp_path / "a-directory"
    directory.mkdir()
    monkeypatch.setenv("SMTP_PASS_FILE", str(directory))
    with caplog.at_level(logging.WARNING):
        result = resolve_secret("SMTP_PASS", default="fallback")
    assert result == "fallback"
    assert any("error reading" in r.message.lower() for r in caplog.records)


# ─── 8. Whitespace trimming ────────────────────────────────────────────────


def test_file_content_trailing_newline_stripped(monkeypatch, tmp_path: Path):
    secret_file = tmp_path / "with-newline"
    secret_file.write_text("secret-value\n")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    assert resolve_secret("SMTP_PASS") == "secret-value"


def test_file_content_surrounding_whitespace_stripped(monkeypatch, tmp_path: Path):
    secret_file = tmp_path / "with-spaces"
    secret_file.write_text("  \n  padded-secret  \n  ")
    monkeypatch.setenv("SMTP_PASS_FILE", str(secret_file))
    assert resolve_secret("SMTP_PASS") == "padded-secret"


def test_env_var_value_stripped(monkeypatch):
    monkeypatch.setenv("SMTP_PASS", "  padded-env-secret  ")
    assert resolve_secret("SMTP_PASS") == "padded-env-secret"


# ─── 9. File path whitespace handling ──────────────────────────────────────


def test_whitespace_only_file_pointer_treated_as_unset(monkeypatch):
    """*_FILE='   ' (whitespace only) should be treated as unset, not a relative path."""
    monkeypatch.setenv("SMTP_PASS_FILE", "   ")
    assert resolve_secret("SMTP_PASS", default="ok") == "ok"


# ─── 10. Generic name support ──────────────────────────────────────────────


def test_works_with_arbitrary_credential_name(monkeypatch, tmp_path: Path):
    """resolve_secret is generic — works for any env-var name, not just the
    three primary credentials."""
    secret_file = tmp_path / "custom"
    secret_file.write_text("custom-value")
    monkeypatch.setenv("TEST_SECRET_RESOLVER_FILE", str(secret_file))
    assert resolve_secret("TEST_SECRET_RESOLVER") == "custom-value"
