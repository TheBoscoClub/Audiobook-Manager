# Audiobook Library API - Modular Architecture

> **This is the production API architecture since v3.2.0.**
>
> The legacy monolithic `api.py` was removed in v3.2.0. This modular architecture is now
> the only supported API implementation.

This package provides a **modular Flask Blueprint-based architecture** for the Audiobook Library API. The original monolithic `api.py` (1994 lines) was refactored into logically separated modules for improved maintainability.

## Architecture Overview

```text
api_modular/
├── __init__.py             # Package initialization, app factory, exports
├── core.py                 # Database connections, CORS, shared utilities
├── audiobooks.py           # Main listing, filtering, streaming endpoints
├── grouped.py              # Grouped queries by author/narrator (v7.0.0+)
├── admin_authors.py        # Author/narrator CRUD management (v7.0.0+)
├── admin_activity.py       # Admin activity audit logging
├── auth.py                 # TOTP authentication and session management
├── user_state.py           # User preferences and state persistence
├── collections.py          # Genre collections and collection routes
├── editions.py             # Edition detection (Dramatized, Full Cast, etc.)
├── duplicates.py           # Duplicate detection and management (with index cleanup)
├── supplements.py          # Companion file (PDF, images) management
├── position_sync.py        # Playback position sync with Audible cloud
├── utilities.py            # Blueprint aggregator for utilities modules
├── utilities_crud.py       # CRUD operations for audiobooks
├── utilities_db.py         # Database maintenance (vacuum, reimport, scan, hashes)
├── utilities_conversion.py # Conversion monitoring with stats
├── utilities_system.py     # System administration (services, upgrades)
├── README.md               # This file
└── MIGRATION.md            # Detailed migration guide
```

## Module Responsibilities

### `core.py` - Shared Utilities

- Database connection factory (`get_db()`)
- CORS header configuration (`add_cors_headers()`)
- Common type definitions (`FlaskResponse`)

### `collections.py` - Genre Collections

- Main genre collections matching database genres (Fiction, Sci-Fi & Fantasy, Mystery & Thriller, etc.)
- Text-search subgenres (Short Stories & Anthologies, Action & Adventure, Historical Fiction)
- Special collections (The Great Courses)
- Dynamic SQL query generators with text pattern matching
- Routes: `/api/collections`, `/api/collections/<name>`

### `editions.py` - Edition Detection

- Identifies special editions from title text
- Supported types: Dramatized, Full Cast, Unabridged, Abridged
- Normalizes base titles for comparison

### `audiobooks.py` - Core Endpoints

- Main audiobook listing with pagination
- Enriched responses include `authors` and `narrators` arrays (v7.0.0+)
- Advanced filtering (genre, narrator, series, etc.)
- Audio streaming with range request support
- Cover image serving from configurable `COVER_DIR`
- Routes: `/api/audiobooks`, `/api/stats`, `/api/filters`, `/api/stream/<id>`, `/covers/<filename>`

### `grouped.py` - Grouped Queries (v7.0.0+)

- Audiobooks grouped by author or narrator using normalized junction tables
- Routes: `/api/audiobooks/grouped?by=author|narrator`

### `admin_authors.py` - Author/Narrator Management (v7.0.0+)

- CRUD operations for normalized author and narrator records
- Merge duplicate authors/narrators
- Routes: `/api/admin/authors/*`, `/api/admin/narrators/*`

### `admin_activity.py` - Admin Activity Logging

- Audit trail for administrative operations
- Routes: `/api/admin/activity`

### `auth.py` - Authentication

- TOTP-based authentication flow
- Session management
- Routes: `/auth/login`, `/auth/logout`, `/auth/admin/*`

### `user_state.py` - User State

- User preference persistence
- Routes: `/api/user/*`

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

### `position_sync.py` - Audible Position Sync (v3.7.2+)

- Bidirectional playback position synchronization with Audible cloud
- "Furthest ahead wins" conflict resolution
- Batch sync for all audiobooks with ASINs
- Position history tracking
- Requires: `audible` library, stored credentials via system keyring
- Routes: `/api/position/*`, `/api/position/sync/*`

### `utilities*.py` - Admin Operations (Modular)

The utilities module is split into focused sub-modules for maintainability:

- **`utilities.py`**: Blueprint aggregator that registers all utility routes
- **`utilities_crud.py`**: Single audiobook CRUD (get, update, delete)
- **`utilities_db.py`**: Database maintenance (vacuum, reimport, export, scan, hash generation)
- **`utilities_conversion.py`**: Conversion monitoring (queue status, active jobs, ETA)
- **`utilities_system.py`**: System administration (services, upgrades, version info)
  - Uses privilege-separated helper pattern for operations requiring root
  - Communicates via `/var/lib/audiobooks/.control/` files
  - Supports: service start/stop/restart, upgrades from GitHub or project

Routes: `/api/utilities/*`, `/api/conversion/*`, `/api/system/*`

## Architecture Details

### Modular Approach (`api_modular/`)

| Aspect | Details |
|--------|---------|
| **File Size** | 18 modules, ~50-900 lines each |
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
    api_port=5001
)

app.run(debug=True)
```

### Production with Waitress

```python
from api_modular import create_app, run_server

app = create_app(...)
run_server(app, port=5001, debug=False, use_waitress=True)
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

```text
AssertionError: The setup method 'route' can no longer be called on the blueprint
```

**Impact:** The modular package cannot be used with test fixtures that create multiple app instances.

**Workaround:** Refactor to create fresh Blueprint instances per app, or restart the Python process between app creations.

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

## Architecture Benefits

The modular architecture provides:

1. Better code organization and maintainability
2. Easier to extend with new features
3. Clear separation of concerns
4. Individual modules can be tested in isolation
5. Foundation for microservices migration

## Files Reference

| File | Primary Responsibility |
|------|----------------------|
| `core.py` | Database, CORS |
| `audiobooks.py` | Core listing/streaming |
| `grouped.py` | Grouped queries by author/narrator |
| `admin_authors.py` | Author/narrator management |
| `admin_activity.py` | Admin activity audit logging |
| `auth.py` | TOTP authentication |
| `user_state.py` | User preferences/state |
| `collections.py` | Genre collections |
| `editions.py` | Edition detection |
| `duplicates.py` | Duplicate detection, index cleanup |
| `supplements.py` | Companion files |
| `position_sync.py` | Audible position sync |
| `utilities.py` | Blueprint aggregator |
| `utilities_crud.py` | Audiobook CRUD |
| `utilities_db.py` | Database maintenance, scan, hashes |
| `utilities_conversion.py` | Conversion monitoring |
| `utilities_system.py` | System administration |
| `__init__.py` | Package init/exports |

## See Also

- [api_server.py](../api_server.py) - Main entry point
