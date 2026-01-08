# Audiobook Library API - Modular Architecture

> ⚠️ **REQUIRED STARTING v3.6.0**
>
> As of v3.5.0, the modular architecture is the **recommended** approach.
> Starting with v3.6.0, it will be **required** - the legacy monolithic `api.py` will be removed.
> Migrate now using: `./migrate-api.sh --to-modular --target /opt/audiobooks`

This package provides a **modular Flask Blueprint-based architecture** for the Audiobook Library API. It refactors the original monolithic `api.py` (1994 lines) into logically separated modules for improved maintainability.

## Architecture Overview

```
api_modular/
├── __init__.py             # Package initialization, app factory, exports
├── core.py                 # Database connections, CORS, shared utilities
├── collections.py          # Genre collections and collection routes
├── editions.py             # Edition detection (Dramatized, Full Cast, etc.)
├── audiobooks.py           # Main listing, filtering, streaming endpoints
├── duplicates.py           # Duplicate detection and management (with index cleanup)
├── supplements.py          # Companion file (PDF, images) management
├── position_sync.py        # Playback position sync with Audible cloud
├── periodicals.py          # Episodic content management (podcasts, news, meditation)
├── utilities.py            # Blueprint aggregator for utilities modules
├── utilities_crud.py       # CRUD operations for audiobooks
├── utilities_db.py         # Database maintenance (vacuum, reimport, export)
├── utilities_ops.py        # Async operations with progress (scan, hashes, checksums)
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
- Advanced filtering (genre, narrator, series, etc.)
- Audio streaming with range request support
- Cover image serving from configurable `COVER_DIR`
- Routes: `/api/audiobooks`, `/api/stats`, `/api/filters`, `/api/stream/<id>`, `/covers/<filename>`

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

### `periodicals.py` - Episodic Content (v3.8.0+)
- Manages Audible episodic content (podcasts, newspapers, meditation series)
- Separate from main audiobook library to avoid clutter
- Parent/child ASIN structure (series → episodes)
- Category filtering: podcast, news, meditation, other
- Real-time sync status via Server-Sent Events (SSE)
- Download queue management
- Routes: `/api/v1/periodicals/*`, `/api/v1/periodicals/sync/*`

### `utilities*.py` - Admin Operations (Modular)
The utilities module is split into focused sub-modules for maintainability:

- **`utilities.py`**: Blueprint aggregator that registers all utility routes
- **`utilities_crud.py`**: Single audiobook CRUD (get, update, delete)
- **`utilities_db.py`**: Database maintenance (vacuum, reimport, export JSON/CSV/DB)
- **`utilities_ops.py`**: Async operations with progress tracking (scan, hashes, checksums)
- **`utilities_conversion.py`**: Conversion monitoring (queue status, active jobs, ETA)
- **`utilities_system.py`**: System administration (services, upgrades, version info)
  - Uses privilege-separated helper pattern for operations requiring root
  - Communicates via `/var/lib/audiobooks/.control/` files
  - Supports: service start/stop/restart, upgrades from GitHub or project

Routes: `/api/utilities/*`, `/api/conversion/*`, `/api/system/*`

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

**Use the modular architecture** (`api_modular/`):

1. It's the future - monolithic `api.py` will be removed in v3.6.0
2. Better code organization and maintainability
3. Easier to extend with new features
4. Clear separation of concerns

The monolithic `api.py` is deprecated and will be removed in v3.6.0. Migrate now:
```bash
./migrate-api.sh --to-modular --target /opt/audiobooks
```

## Files Reference

| File | Lines | Primary Responsibility |
|------|-------|----------------------|
| `core.py` | ~50 | Database, CORS |
| `collections.py` | ~230 | Genre collections |
| `editions.py` | ~145 | Edition detection |
| `audiobooks.py` | ~470 | Core listing/streaming |
| `duplicates.py` | ~750 | Duplicate detection, index cleanup |
| `supplements.py` | ~190 | Companion files |
| `position_sync.py` | ~600 | Audible position sync |
| `periodicals.py` | ~400 | Episodic content (podcasts, news) |
| `utilities.py` | ~60 | Blueprint aggregator |
| `utilities_crud.py` | ~260 | Audiobook CRUD |
| `utilities_db.py` | ~290 | Database maintenance |
| `utilities_ops.py` | ~470 | Async operations, checksums |
| `utilities_conversion.py` | ~300 | Conversion monitoring |
| `utilities_system.py` | ~430 | System administration |
| `__init__.py` | ~200 | Package init/exports |

## Switching Architectures

Use `migrate-api.sh` to switch between monolithic and modular architectures:

```bash
# Check current architecture
./migrate-api.sh --status

# Switch to modular
./migrate-api.sh --to-modular --target /opt/audiobooks

# Switch back to monolithic
./migrate-api.sh --to-monolithic --target /opt/audiobooks
```

**Note:** Migration automatically stops services, updates wrapper scripts, and restarts services.

## See Also

- [MIGRATION.md](./MIGRATION.md) - Detailed migration instructions
- [api.py](../api.py) - Original monolithic implementation
- [api_server.py](../api_server.py) - Modular entry point
