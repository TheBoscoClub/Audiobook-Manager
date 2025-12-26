"""
Tests for the configuration module.
"""
import os
from pathlib import Path

import pytest


class TestConfigLoading:
    """Test configuration file loading and parsing."""

    def test_load_config_file_nonexistent(self, temp_dir):
        """Test loading a non-existent config file returns empty dict."""
        from config import _load_config_file
        result = _load_config_file(temp_dir / "nonexistent.conf")
        assert result == {}

    def test_load_config_file_basic(self, temp_dir):
        """Test loading a basic config file."""
        from config import _load_config_file

        config_file = temp_dir / "test.conf"
        config_file.write_text("KEY1=value1\nKEY2=value2\n")

        result = _load_config_file(config_file)
        assert result['KEY1'] == 'value1'
        assert result['KEY2'] == 'value2'

    def test_load_config_file_with_quotes(self, temp_dir):
        """Test loading config with quoted values."""
        from config import _load_config_file

        config_file = temp_dir / "test.conf"
        config_file.write_text('KEY1="quoted value"\nKEY2=\'single quoted\'\n')

        result = _load_config_file(config_file)
        assert result['KEY1'] == 'quoted value'
        assert result['KEY2'] == 'single quoted'

    def test_load_config_file_comments(self, temp_dir):
        """Test that comments are ignored."""
        from config import _load_config_file

        config_file = temp_dir / "test.conf"
        config_file.write_text("# This is a comment\nKEY=value\n# Another comment\n")

        result = _load_config_file(config_file)
        assert len(result) == 1
        assert result['KEY'] == 'value'

    def test_load_config_file_empty_lines(self, temp_dir):
        """Test that empty lines are skipped."""
        from config import _load_config_file

        config_file = temp_dir / "test.conf"
        config_file.write_text("\n\nKEY=value\n\n")

        result = _load_config_file(config_file)
        assert result['KEY'] == 'value'


class TestGetConfig:
    """Test the get_config function."""

    def test_get_config_with_env_override(self, monkeypatch):
        """Test that environment variables override config file values."""
        from config import get_config

        monkeypatch.setenv('TEST_VAR', 'env_value')
        result = get_config('TEST_VAR', 'default')
        assert result == 'env_value'

    def test_get_config_default(self):
        """Test that default is returned when key not found."""
        from config import get_config

        result = get_config('NONEXISTENT_KEY_12345', 'my_default')
        assert result == 'my_default'


class TestConfigPaths:
    """Test that configuration paths are properly set."""

    def test_audiobooks_home_is_path(self):
        """Test AUDIOBOOKS_HOME is a Path object."""
        from config import AUDIOBOOKS_HOME
        assert isinstance(AUDIOBOOKS_HOME, Path)

    def test_audiobooks_library_is_path(self):
        """Test AUDIOBOOKS_LIBRARY is a Path object."""
        from config import AUDIOBOOKS_LIBRARY
        assert isinstance(AUDIOBOOKS_LIBRARY, Path)

    def test_audiobooks_database_is_path(self):
        """Test AUDIOBOOKS_DATABASE is a Path object."""
        from config import AUDIOBOOKS_DATABASE
        assert isinstance(AUDIOBOOKS_DATABASE, Path)

    def test_api_port_is_int(self):
        """Test AUDIOBOOKS_API_PORT is an integer."""
        from config import AUDIOBOOKS_API_PORT
        assert isinstance(AUDIOBOOKS_API_PORT, int)
        assert AUDIOBOOKS_API_PORT > 0

    def test_web_port_is_int(self):
        """Test AUDIOBOOKS_WEB_PORT is an integer."""
        from config import AUDIOBOOKS_WEB_PORT
        assert isinstance(AUDIOBOOKS_WEB_PORT, int)
        assert AUDIOBOOKS_WEB_PORT > 0


class TestPrintConfig:
    """Test the print_config utility function."""

    def test_print_config_runs(self, capsys):
        """Test that print_config runs without error."""
        from config import print_config
        print_config()
        captured = capsys.readouterr()
        assert 'AUDIOBOOKS_HOME' in captured.out
        assert 'AUDIOBOOKS_DATA' in captured.out


class TestCheckDirs:
    """Test the check_dirs utility function."""

    def test_check_dirs_returns_bool(self):
        """Test that check_dirs returns a boolean."""
        from config import check_dirs
        result = check_dirs()
        assert isinstance(result, bool)
