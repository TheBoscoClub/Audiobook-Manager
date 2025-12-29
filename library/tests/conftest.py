"""
Pytest configuration and shared fixtures for Audiobooks Library tests.
"""

import sys
import tempfile
from pathlib import Path

import pytest

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config_env(temp_dir):
    """Create a mock config.env file."""
    config_file = temp_dir / "config.env"
    config_file.write_text("""
# Test configuration
AUDIOBOOKS_DATA=/test/audiobooks
AUDIOBOOKS_LIBRARY=/test/audiobooks/Library
AUDIOBOOKS_SOURCES=/test/audiobooks/Sources
AUDIOBOOKS_API_PORT=5001
""")
    return config_file


@pytest.fixture
def sample_audiobook_data():
    """Sample audiobook data for testing."""
    return {
        "id": 1,
        "title": "Test Audiobook",
        "author": "Test Author",
        "narrator": "Test Narrator",
        "duration_hours": 10.5,
        "file_path": "/test/path/audiobook.opus",
        "asin": "B00TEST123",
    }


@pytest.fixture
def app_client():
    """Create a test client for the Flask API."""
    from backend.api_modular import app

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
