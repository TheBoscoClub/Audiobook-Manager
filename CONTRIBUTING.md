# Contributing to Audiobooks

Thank you for your interest in contributing to the Audiobooks project!

## Getting Started

1. **Fork the repository** and clone it locally

2. **Install git hooks** (required):
   ```bash
   ./scripts/install-hooks.sh
   ```
   This installs commit safeguards that enforce coding standards.

3. **Install dependencies**:
   ```bash
   cd library
   python -m venv venv
   source venv/bin/activate  # or `venv\Scripts\activate` on Windows
   pip install -r requirements.txt
   pip install pytest pytest-cov ruff
   ```

4. **Run tests** to ensure everything works:
   ```bash
   cd library
   pytest tests/ -v
   ```

## CRITICAL: No Hardcoded Paths

**All paths MUST use configuration variables.** This is enforced by a pre-commit hook.

### The Rule

- **NEVER** write literal paths like `/var/lib/audiobooks`, `/srv/audiobooks`, `/run/audiobooks`
- **ALWAYS** use environment variables: `$AUDIOBOOKS_DATA`, `$AUDIOBOOKS_VAR_DIR`, etc.
- If a path variable doesn't exist, **add it** to `lib/audiobooks-config.sh` first

### Why This Matters

End users configure their own paths. Hardcoded paths:
- Break user customization
- Cause silent failures when paths differ
- Have repeatedly caused regressions in past releases

### Available Variables

| Variable | Purpose |
|----------|---------|
| `AUDIOBOOKS_DATA` | Main data directory |
| `AUDIOBOOKS_LIBRARY` | Converted audiobooks |
| `AUDIOBOOKS_SOURCES` | Source files |
| `AUDIOBOOKS_RUN_DIR` | Runtime (locks, FIFOs) |
| `AUDIOBOOKS_VAR_DIR` | Persistent state |
| `AUDIOBOOKS_STAGING` | Conversion staging |
| `AUDIOBOOKS_DATABASE` | SQLite database |
| `AUDIOBOOKS_LOGS` | Log files |

See `lib/audiobooks-config.sh` for the complete list with defaults.

---

## Development Workflow

### Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check for issues
ruff check library/

# Auto-fix issues
ruff check library/ --fix

# Format code
ruff format library/
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=backend --cov=scanner --cov-report=term-missing

# Run specific test file
pytest tests/test_api_modular.py -v
```

### Project Structure

```
audiobooks/
├── library/              # Main application
│   ├── backend/          # Flask API
│   │   ├── api_server.py # Server launcher
│   │   └── api_modular/  # Modular Flask Blueprints
│   ├── scanner/          # Audiobook scanning
│   ├── scripts/          # Utility scripts
│   ├── web-v2/           # Web interface
│   └── tests/            # Test suite
├── converter/            # AAXtoMP3 fork
├── systemd/              # Service files
└── docker-compose.yml    # Container orchestration
```

## Pull Request Process

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** and ensure:
   - Tests pass (`pytest tests/ -v`)
   - Linting passes (`ruff check library/`)
   - Code is formatted (`ruff format library/`)

3. **Write meaningful commit messages**:
   ```
   Add feature X for Y

   - Detail about change 1
   - Detail about change 2
   ```

4. **Push and create a Pull Request**

5. **Describe your changes** in the PR:
   - What does this PR do?
   - How was it tested?
   - Any breaking changes?

## Reporting Issues

When reporting bugs, please include:

- Python version (`python --version`)
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Relevant log output

## Security Issues

For security vulnerabilities, please see [SECURITY.md](SECURITY.md) for our responsible disclosure policy.

## Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Focus on the code, not the person
- Help others learn and grow

## Questions?

Open an issue with the "question" label or reach out to the maintainers.

Thank you for contributing!
