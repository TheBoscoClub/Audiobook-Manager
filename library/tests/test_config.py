"""
Tests for the configuration module.
"""

from pathlib import Path


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
        assert result["KEY1"] == "value1"
        assert result["KEY2"] == "value2"

    def test_load_config_file_with_quotes(self, temp_dir):
        """Test loading config with quoted values."""
        from config import _load_config_file

        config_file = temp_dir / "test.conf"
        config_file.write_text("KEY1=\"quoted value\"\nKEY2='single quoted'\n")

        result = _load_config_file(config_file)
        assert result["KEY1"] == "quoted value"
        assert result["KEY2"] == "single quoted"

    def test_load_config_file_comments(self, temp_dir):
        """Test that comments are ignored."""
        from config import _load_config_file

        config_file = temp_dir / "test.conf"
        config_file.write_text("# This is a comment\nKEY=value\n# Another comment\n")

        result = _load_config_file(config_file)
        assert len(result) == 1
        assert result["KEY"] == "value"

    def test_load_config_file_empty_lines(self, temp_dir):
        """Test that empty lines are skipped."""
        from config import _load_config_file

        config_file = temp_dir / "test.conf"
        config_file.write_text("\n\nKEY=value\n\n")

        result = _load_config_file(config_file)
        assert result["KEY"] == "value"


class TestGetConfig:
    """Test the get_config function."""

    def test_get_config_with_env_override(self, monkeypatch):
        """Test that environment variables override config file values."""
        from config import get_config

        monkeypatch.setenv("TEST_VAR", "env_value")
        result = get_config("TEST_VAR", "default")
        assert result == "env_value"

    def test_get_config_default(self):
        """Test that default is returned when key not found."""
        from config import get_config

        result = get_config("NONEXISTENT_KEY_12345", "my_default")
        assert result == "my_default"


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
        assert "AUDIOBOOKS_HOME" in captured.out
        assert "AUDIOBOOKS_DATA" in captured.out


class TestCheckDirs:
    """Test the check_dirs utility function."""

    def test_check_dirs_returns_bool(self):
        """Test that check_dirs returns a boolean."""
        from config import check_dirs

        result = check_dirs()
        assert isinstance(result, bool)


class TestNoHardcodedPaths:
    """Test that Python files don't contain hardcoded paths.

    Hardcoded paths cause issues when the app is deployed to different
    environments. All paths should use configuration variables.
    """

    # Paths that should be configured, not hardcoded
    # Note: /etc/audiobooks is NOT forbidden - it's the proper FHS system config location
    FORBIDDEN_PATHS = [
        "/var/lib/audiobooks",
        "/opt/audiobooks",
        "/srv/audiobooks",
        "/hddRaid1/Audiobooks",
    ]

    # Files/patterns that are allowed to have these paths (e.g., config defaults)
    ALLOWED_FILES = [
        "config.py",  # Config module defines defaults
        "audiobook-config.sh",  # Shell config defines defaults
        "test_",  # Test files may use mock/fixture paths
        ".conf",  # Config files
        ".service",  # Systemd files MUST use literal paths (no shell expansion)
        ".timer",  # Systemd timer files
        "CLAUDE.md",  # Documentation
        "README.md",  # Documentation
        "audiobook-help",  # Help output shows paths to user
        "upgrade-helper",  # Bootstrap scripts run before config is available
        "cli.py",  # CLI tools use argparse defaults with env var fallbacks
    ]

    def _should_skip_file(self, filepath: Path) -> bool:
        """Check if file should be skipped from hardcoded path check."""
        for allowed in self.ALLOWED_FILES:
            if allowed in str(filepath):
                return True
        return False

    def _scan_file_for_hardcoded_paths(
        self, filepath: Path
    ) -> list[tuple[int, str, str]]:
        """Scan a file for hardcoded paths.

        Returns list of (line_number, path_found, line_content).
        """
        violations = []
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            for line_num, line in enumerate(content.splitlines(), 1):
                # Skip comments
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                # Check for forbidden paths
                for forbidden in self.FORBIDDEN_PATHS:
                    if forbidden in line:
                        # Skip if it's in a config.get() default or environment variable fallback
                        # These are acceptable as they're fallback defaults
                        if "get_config(" in line or "environ.get(" in line:
                            continue
                        # Skip bash variable defaults: ${VAR:-/default/path}
                        # This is the proper way to specify fallbacks in shell
                        if (
                            ":-" in line
                            and forbidden in line.split(":-")[1].split("}")[0]
                            if ":-" in line
                            else False
                        ):
                            continue
                        # Skip config file sourcing bootstrap (needed before config is loaded)
                        if "audiobook-config.sh" in line:
                            continue
                        violations.append((line_num, forbidden, line.strip()[:100]))
        except Exception:
            pass  # Skip files that can't be read
        return violations

    def test_library_python_files_no_hardcoded_paths(self):
        """Test that library Python files use config variables, not hardcoded paths."""
        from pathlib import Path as P

        # Find project root
        test_dir = P(__file__).parent
        library_dir = test_dir.parent

        violations = []
        for py_file in library_dir.rglob("*.py"):
            if self._should_skip_file(py_file):
                continue
            if "__pycache__" in str(py_file):
                continue

            file_violations = self._scan_file_for_hardcoded_paths(py_file)
            for line_num, path, content in file_violations:
                rel_path = py_file.relative_to(library_dir)
                violations.append(f"{rel_path}:{line_num}: {path}\n    {content}")

        assert not violations, (
            f"Found {len(violations)} hardcoded path(s) in library Python files. "
            f"Use config module variables instead:\n" + "\n".join(violations)
        )

    def test_rnd_python_files_no_hardcoded_paths(self):
        """Test that rnd (research) Python files use config variables."""
        from pathlib import Path as P

        test_dir = P(__file__).parent
        rnd_dir = test_dir.parent.parent / "rnd"

        if not rnd_dir.exists():
            return  # Skip if rnd directory doesn't exist

        violations = []
        for py_file in rnd_dir.rglob("*.py"):
            if self._should_skip_file(py_file):
                continue
            if "__pycache__" in str(py_file):
                continue

            file_violations = self._scan_file_for_hardcoded_paths(py_file)
            for line_num, path, content in file_violations:
                violations.append(f"{py_file.name}:{line_num}: {path}\n    {content}")

        assert not violations, (
            f"Found {len(violations)} hardcoded path(s) in rnd Python files. "
            f"Use config module variables instead:\n" + "\n".join(violations)
        )

    def test_scripts_python_files_no_hardcoded_paths(self):
        """Test that scripts Python files use config variables."""
        from pathlib import Path as P

        test_dir = P(__file__).parent
        scripts_dir = test_dir.parent / "scripts"

        if not scripts_dir.exists():
            return  # Skip if scripts directory doesn't exist

        violations = []
        for py_file in scripts_dir.rglob("*.py"):
            if self._should_skip_file(py_file):
                continue
            if "__pycache__" in str(py_file):
                continue

            file_violations = self._scan_file_for_hardcoded_paths(py_file)
            for line_num, path, content in file_violations:
                violations.append(f"{py_file.name}:{line_num}: {path}\n    {content}")

        assert not violations, (
            f"Found {len(violations)} hardcoded path(s) in scripts Python files. "
            f"Use config module variables instead:\n" + "\n".join(violations)
        )

    def test_shell_scripts_no_hardcoded_paths(self):
        """Test that shell scripts use config variables, not hardcoded paths.

        Shell scripts should source audiobook-config.sh and use variables like
        $AUDIOBOOKS_DATA, $AUDIOBOOKS_VAR_DIR, etc.
        """
        from pathlib import Path as P

        test_dir = P(__file__).parent
        project_root = test_dir.parent.parent
        scripts_dir = project_root / "scripts"

        if not scripts_dir.exists():
            return  # Skip if scripts directory doesn't exist

        violations = []
        for sh_file in scripts_dir.glob("*"):
            # Only check executable files without extensions (shell scripts)
            if sh_file.is_dir() or sh_file.suffix:
                continue
            if self._should_skip_file(sh_file):
                continue

            file_violations = self._scan_file_for_hardcoded_paths(sh_file)
            for line_num, path, content in file_violations:
                violations.append(f"{sh_file.name}:{line_num}: {path}\n    {content}")

        assert not violations, (
            f"Found {len(violations)} hardcoded path(s) in shell scripts. "
            f"Use config variables from audiobook-config.sh instead:\n"
            + "\n".join(violations)
        )

    def test_systemd_files_no_hardcoded_paths(self):
        """Test that systemd files use expected paths.

        Note: Systemd unit files MUST use literal paths because systemd
        doesn't support shell variable expansion. This test documents the
        expected paths rather than flagging them as violations.
        """
        from pathlib import Path as P

        test_dir = P(__file__).parent
        project_root = test_dir.parent.parent
        systemd_dir = project_root / "systemd"

        if not systemd_dir.exists():
            return  # Skip if systemd directory doesn't exist

        # Systemd files are exempt - they must use literal paths
        # This test just verifies they exist and are parseable
        service_files = list(systemd_dir.glob("*.service"))
        assert len(service_files) > 0, "No systemd service files found"

        for service_file in service_files:
            content = service_file.read_text()
            # Basic validation - should have [Unit] and [Service] sections
            assert "[Unit]" in content, f"{service_file.name} missing [Unit] section"
            assert "[Service]" in content, (
                f"{service_file.name} missing [Service] section"
            )


class TestConfigVariablesUsed:
    """Test that key modules import and use configuration variables."""

    def test_maintenance_module_uses_config(self):
        """Test maintenance.py imports config variables."""
        from pathlib import Path as P

        module_path = (
            P(__file__).parent.parent
            / "backend"
            / "api_modular"
            / "utilities_ops"
            / "maintenance.py"
        )
        if not module_path.exists():
            return

        content = module_path.read_text()
        assert "from config import" in content, (
            "maintenance.py should import from config module"
        )
        assert "AUDIOBOOKS_DATABASE" in content, (
            "maintenance.py should use AUDIOBOOKS_DATABASE config variable"
        )


class TestInstalledAppConfig:
    """Test configuration in the installed production app.

    These tests verify that the production installation at /opt/audiobooks
    correctly uses configuration variables.
    """

    PRODUCTION_PATH = Path("/opt/audiobooks")

    def test_installed_app_exists(self):
        """Test that the production installation exists."""
        if not self.PRODUCTION_PATH.exists():
            import pytest

            pytest.skip("Production installation not found at /opt/audiobooks")

        assert (self.PRODUCTION_PATH / "library").exists(), (
            "Production installation missing library directory"
        )

    def test_installed_config_module_exists(self):
        """Test that config.py exists in production."""
        config_path = self.PRODUCTION_PATH / "library" / "config.py"
        if not self.PRODUCTION_PATH.exists():
            import pytest

            pytest.skip("Production installation not found")

        assert config_path.exists(), "Production installation missing config.py"

    def test_installed_rnd_scripts_use_config(self):
        """Test that installed rnd scripts import config module."""
        rnd_path = self.PRODUCTION_PATH / "rnd"
        if not rnd_path.exists():
            import pytest

            pytest.skip("Production rnd directory not found")

        for py_file in rnd_path.glob("populate_asins*.py"):
            content = py_file.read_text()
            assert "from config import" in content, (
                f"Installed {py_file.name} should import from config module"
            )

    def test_installed_maintenance_uses_config(self):
        """Test that installed maintenance.py imports config module."""
        maint_path = (
            self.PRODUCTION_PATH
            / "library"
            / "backend"
            / "api_modular"
            / "utilities_ops"
            / "maintenance.py"
        )
        if not maint_path.exists():
            import pytest

            pytest.skip("Production maintenance.py not found")

        content = maint_path.read_text()
        assert "from config import" in content, (
            "Installed maintenance.py should import from config module"
        )
