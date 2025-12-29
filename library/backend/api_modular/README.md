# Audiobook Library API - Modular Architecture

This package provides a **modular Flask Blueprint-based architecture** for the Audiobook Library API. It refactors the original monolithic `api.py` (1994 lines) into logically separated modules for improved maintainability.

## Architecture Overview

```
api_modular/
├── __init__.py       # Package initialization, app factory, exports
├── core.py           # Database connections, CORS, shared utilities
├── collections.py    # Genre collections and collection routes
├── editions.py       # Edition detection (Dramatized, Full Cast, etc.)
├── audiobooks.py     # Main listing, filtering, streaming endpoints
├── duplicates.py     # Duplicate detection and management
├── supplements.py    # Companion file (PDF, images) management
├── utilities.py      # Admin operations (bulk update, export, rescan)
├── README.md         # This file
└── MIGRATION.md      # Detailed migration guide
```

## Module Responsibilities

### `core.py` - Shared Utilities
- Database connection factory (`get_db()`)
- CORS header configuration (`add_cors_headers()`)
- Common type definitions (`FlaskResponse`)

### `collections.py` - Genre Collections
- 14 predefined genre collections (Classics, Horror, SciFi, etc.)
- Dynamic SQL query generators for genres
- Routes: `/api/collections`, `/api/collections/<name>`

### `editions.py` - Edition Detection
- Identifies special editions from title text
- Supported types: Dramatized, Full Cast, Unabridged, Abridged
- Normalizes base titles for comparison

### `audiobooks.py` - Core Endpoints
- Main audiobook listing with pagination
- Advanced filtering (genre, narrator, series, etc.)
- Audio streaming with range request support
- Cover image serving
- Routes: `/api/audiobooks`, `/api/stats`, `/api/filters`, `/api/stream/<id>`

### `duplicates.py` - Duplicate Management
- Hash-based duplicate detection
- Title-based duplicate grouping
- Bulk duplicate operations
- Routes: `/api/duplicates`, `/api/hash-stats`

### `supplements.py` - Companion Files
- PDF, image, and document management
- Per-audiobook supplement listing
- File download endpoints
- Routes: `/api/supplements`, `/api/audiobooks/<id>/supplements`

### `utilities.py` - Admin Operations
- Single and bulk audiobook updates
- Database maintenance (vacuum, rescan, reimport)
- Export functions (JSON, CSV, SQL dump)
- Routes: `/api/utilities/*`, bulk operations

## Comparison: Monolithic vs Modular

### Monolithic Approach (`api.py`)

| Aspect | Details |
|--------|---------|
| **File Size** | 1994 lines, single file |
| **Deployment** | Simple - one file to deploy |
| **Testing** | All tests patch `backend.api.*` |
| **Production Status** | Battle-tested, all 234 tests pass |
| **Best For** | Small teams, simple deployments, proven stability |

**Pros:**
- Zero configuration required
- Single point of truth for all routes
- No import complexity
- Test mocking paths are straightforward
- Proven in production

**Cons:**
- Difficult to navigate (nearly 2000 lines)
- Hard to find specific functionality
- Merge conflicts more likely with multiple developers
- All routes load at startup even if unused
- Harder to unit test individual components

### Modular Approach (`api_modular/`)

| Aspect | Details |
|--------|---------|
| **File Size** | 8 files, ~200-450 lines each |
| **Deployment** | Directory with multiple modules |
| **Testing** | Requires updated mock paths |
| **Production Status** | Reference implementation, needs test updates |
| **Best For** | Larger teams, microservice migration prep |

**Pros:**
- Clear separation of concerns
- Easier code navigation
- Better git history per feature area
- Enables parallel development
- Individual modules can be tested in isolation
- Foundation for microservices migration

**Cons:**
- More complex import structure
- Requires test mock path updates
- Blueprint registration limitation (see Cautions)
- Additional files to track
- Slightly more complex deployment

## Usage

### Using the Modular Package

```python
from api_modular import create_app

app = create_app(
    database_path=Path("/path/to/audiobooks.db"),
    project_dir=Path("/path/to/audiobook/files"),
    supplements_dir=Path("/path/to/supplements"),
    api_port=5000
)

app.run(debug=True)
```

### Production with Waitress

```python
from api_modular import create_app, run_server

app = create_app(...)
run_server(app, port=5000, debug=False, use_waitress=True)
```

### Entry Point Script

Use `api_server.py` as the main entry point:

```bash
# Development (from project directory)
cd library/backend
python api_server.py

# Production (system installation)
cd /opt/audiobooks/library/backend
python api_server.py
```

## Cautions and Known Limitations

### 1. Blueprint Registration Limitation

**Issue:** Flask blueprints are module-level objects. Calling `create_app()` multiple times (e.g., in test fixtures) will attempt to add routes to already-registered blueprints.

**Error:**
```
AssertionError: The setup method 'route' can no longer be called on the blueprint
```

**Impact:** The modular package cannot be used with test fixtures that create multiple app instances.

**Workaround:** Use the original `api.py` for testing, or refactor to create fresh Blueprint instances per app.

### 2. Test Mock Paths

**Issue:** Existing tests patch paths like `backend.api.send_file`. The modular package requires different paths.

**If migrating tests:**
```python
# Old (monolithic)
@patch('backend.api.send_file')

# New (modular)
@patch('backend.api_modular.audiobooks.send_file')
```

### 3. Import Order Matters

The package's `__init__.py` imports modules in a specific order to avoid circular dependencies. Do not modify import order without testing.

### 4. Database Path Configuration

Each module receives the database path through Flask's `app.config`. Ensure `DATABASE_PATH` is set before any route is accessed.

## Performance Considerations

- **Startup:** Both approaches have similar startup times. Flask loads all blueprints at initialization regardless.
- **Runtime:** Identical performance - routes execute the same code.
- **Memory:** Negligible difference - Python loads all modules on first import.

## Recommended Approach

For most use cases, **continue using the monolithic `api.py`**:

1. It's production-tested with 234 passing tests
2. Simpler deployment and configuration
3. No test updates required
4. Systemd service already configured

Consider the modular approach when:
- Multiple developers work on different API areas
- You're planning a microservices migration
- You need isolated unit tests for specific endpoints
- Code navigation in a 2000-line file becomes painful

## Files Reference

| File | Lines | Primary Responsibility |
|------|-------|----------------------|
| `core.py` | ~50 | Database, CORS |
| `collections.py` | ~180 | Genre collections |
| `editions.py` | ~145 | Edition detection |
| `audiobooks.py` | ~350 | Core listing/streaming |
| `duplicates.py` | ~380 | Duplicate detection |
| `supplements.py` | ~190 | Companion files |
| `utilities.py` | ~450 | Admin operations |
| `__init__.py` | ~200 | Package init/exports |

## See Also

- [MIGRATION.md](./MIGRATION.md) - Detailed migration instructions
- [api.py](../api.py) - Original monolithic implementation
- [api_server.py](../api_server.py) - Modular entry point
