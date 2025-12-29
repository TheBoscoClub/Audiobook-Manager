# Migration Guide: Monolithic to Modular API

This guide provides step-by-step instructions for migrating from the monolithic `api.py` to the modular `api_modular/` package.

## Table of Contents

1. [Pre-Migration Checklist](#pre-migration-checklist)
2. [Migration Steps](#migration-steps)
3. [Test Migration](#test-migration)
4. [Systemd Service Updates](#systemd-service-updates)
5. [Rollback Procedure](#rollback-procedure)
6. [Troubleshooting](#troubleshooting)

---

## Pre-Migration Checklist

Before migrating, ensure:

- [ ] All current tests pass (`pytest library/` shows 234/234 passing)
- [ ] You have a backup of the current working state
- [ ] No active users on the API server
- [ ] Database backup exists
- [ ] You understand the [known limitations](./README.md#cautions-and-known-limitations)

### Verify Current State

```bash
# From project root directory
cd <project-root>

# Run all tests
pytest library/ -v

# Check API is working
curl http://localhost:5000/health
curl http://localhost:5000/api/stats
```

---

## Migration Steps

### Step 1: Verify Package Structure

Ensure all modular files exist:

```bash
ls -la library/backend/api_modular/
```

Expected files:
```
__init__.py
core.py
collections.py
editions.py
audiobooks.py
duplicates.py
supplements.py
utilities.py
README.md
MIGRATION.md
```

### Step 2: Test the Modular Package

Before switching production, verify the modular package works:

```bash
cd library/backend

# Start using the modular entry point
python api_server.py
```

In another terminal:
```bash
# Test endpoints
curl http://localhost:5000/health
curl http://localhost:5000/api/stats
curl http://localhost:5000/api/audiobooks?limit=5
```

### Step 3: Update Entry Point (Manual Test)

Test running directly:

```python
# Python REPL test
>>> import sys
>>> sys.path.insert(0, '<project-root>/library')  # e.g., /opt/audiobooks/library
>>> from config import DATABASE_PATH, API_PORT, PROJECT_DIR, SUPPLEMENTS_DIR
>>> from backend.api_modular import create_app
>>> app = create_app(DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR, API_PORT)
>>> with app.test_client() as client:
...     response = client.get('/health')
...     print(response.json)
{'status': 'healthy'}
```

### Step 4: Stop Current Service

```bash
sudo systemctl stop audiobooks-api
```

### Step 5: Update Systemd Service

Edit the service file to use the new entry point:

```bash
sudo nano /etc/systemd/system/audiobooks-api.service
```

Change the `ExecStart` line:

**Before (monolithic):**
```ini
ExecStart=/opt/audiobooks/.venv/bin/python /opt/audiobooks/library/backend/api.py
```

**After (modular):**
```ini
ExecStart=/opt/audiobooks/.venv/bin/python /opt/audiobooks/library/backend/api_server.py
```

### Step 6: Reload and Start

```bash
sudo systemctl daemon-reload
sudo systemctl start audiobooks-api
sudo systemctl status audiobooks-api
```

### Step 7: Verify Production

```bash
# Health check
curl http://localhost:5000/health

# Stats check
curl http://localhost:5000/api/stats

# List audiobooks
curl "http://localhost:5000/api/audiobooks?limit=5"
```

---

## Test Migration

### The Challenge

Existing tests mock paths like `backend.api.send_file`. The modular package has different import paths.

### Option A: Keep Tests as-is (Recommended)

The original `api.py` remains in place. Tests continue to work against it without modification. The modular package exists alongside as a reference implementation.

**This is the recommended approach** until you're ready to fully commit to the modular architecture.

### Option B: Update Test Mock Paths

If you want tests to use the modular package, update mock decorators:

**Audiobook streaming tests:**
```python
# Before
@patch('backend.api.send_file')
def test_stream_audiobook(self, mock_send_file):

# After
@patch('backend.api_modular.audiobooks.send_file')
def test_stream_audiobook(self, mock_send_file):
```

**Supplement download tests:**
```python
# Before
@patch('backend.api.send_file')
def test_supplement_download(self, mock_send_file):

# After
@patch('backend.api_modular.supplements.send_file')
def test_supplement_download(self, mock_send_file):
```

**Database tests:**
```python
# Before
from backend.api import app, get_db

# After
from backend.api_modular import app, get_db
```

### Option C: Dual Testing

Run tests against both implementations:

```python
# conftest.py
import pytest

@pytest.fixture(params=['monolithic', 'modular'])
def api_app(request):
    if request.param == 'monolithic':
        from backend.api import app
    else:
        from backend.api_modular import create_app
        from config import DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR
        app = create_app(DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR, 5000)
    return app
```

---

## Systemd Service Updates

### Full Service File Example

```ini
[Unit]
Description=Audiobook Library API (Modular)
After=network.target

[Service]
Type=simple
User=audiobooks
Group=audiobooks
WorkingDirectory=/opt/audiobooks/library/backend
Environment=FLASK_DEBUG=false
Environment=AUDIOBOOKS_USE_WAITRESS=true
ExecStart=/opt/audiobooks/.venv/bin/python /opt/audiobooks/library/backend/api_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Key Changes from Monolithic

| Setting | Monolithic | Modular |
|---------|-----------|---------|
| ExecStart | `.../api.py` | `.../api_server.py` |
| Description | (any) | Add "(Modular)" for clarity |

Everything else remains identical.

---

## Rollback Procedure

If issues occur, rollback is simple:

### Quick Rollback

```bash
# Stop service
sudo systemctl stop audiobooks-api

# Edit service file
sudo nano /etc/systemd/system/audiobooks-api.service

# Change ExecStart back to:
# ExecStart=/opt/audiobooks/.venv/bin/python /opt/audiobooks/library/backend/api.py

# Reload and start
sudo systemctl daemon-reload
sudo systemctl start audiobooks-api
```

### Verify Rollback

```bash
curl http://localhost:5000/health
curl http://localhost:5000/api/stats
```

---

## Troubleshooting

### Error: Blueprint Route Registration

**Symptom:**
```
AssertionError: The setup method 'route' can no longer be called on the blueprint
```

**Cause:** `create_app()` was called multiple times (e.g., in test fixtures).

**Solution:**
1. Use the monolithic `api.py` for tests
2. Or restart the Python process between app creations
3. Or refactor to create fresh Blueprint instances (requires code changes)

### Error: Import Module Not Found

**Symptom:**
```
ModuleNotFoundError: No module named 'api_modular'
```

**Cause:** Python path doesn't include the backend directory.

**Solution:**
```python
import sys
sys.path.insert(0, '<install-dir>/library/backend')  # e.g., /opt/audiobooks/library/backend
from api_modular import create_app
```

Or set PYTHONPATH:
```bash
export PYTHONPATH=<install-dir>/library/backend:$PYTHONPATH  # e.g., /opt/audiobooks/library/backend
```

### Error: Database Not Found

**Symptom:**
```
Error: Database not found at /path/to/audiobooks.db
```

**Cause:** Configuration not loading correctly.

**Solution:**
Verify `config.py` exports `DATABASE_PATH` correctly and the database file exists.

### Error: CORS Preflight Failing

**Symptom:** Browser shows CORS errors on OPTIONS requests.

**Cause:** CORS headers not being added to preflight responses.

**Solution:** The modular package applies CORS via `@app.after_request`. Ensure the app is created with proper initialization:

```python
app = create_app(database_path, project_dir, supplements_dir, api_port)
# CORS is configured in __init__.py's create_app function
```

### Error: Streaming Returns 404

**Symptom:** `/api/stream/<id>` returns 404.

**Cause:** Blueprint not registered properly.

**Solution:** Check that `init_audiobooks_routes()` was called during app creation. The `create_app()` function should handle this automatically.

---

## Migration Timeline Recommendation

| Phase | Duration | Activities |
|-------|----------|-----------|
| **Evaluation** | 1 week | Run modular package in dev, compare behavior |
| **Parallel Run** | 2 weeks | Run both versions, compare logs |
| **Soft Migration** | 1 week | Switch production, monitor closely |
| **Cleanup** | When stable | Consider removing `api.py` (optional) |

### Keep Both Indefinitely

There's no requirement to remove `api.py`. Many projects keep both:
- `api.py` - Simple, proven, good for quick fixes
- `api_modular/` - Structured, good for feature development

---

## Post-Migration Verification

After successful migration, verify all functionality:

```bash
# Core endpoints
curl http://localhost:5000/health
curl http://localhost:5000/api/stats
curl http://localhost:5000/api/audiobooks?limit=10
curl http://localhost:5000/api/filters

# Collections
curl http://localhost:5000/api/collections
curl http://localhost:5000/api/collections/classics

# Duplicates
curl http://localhost:5000/api/hash-stats
curl http://localhost:5000/api/duplicates

# Supplements
curl http://localhost:5000/api/supplements/stats

# Streaming (requires valid audiobook ID)
curl -I "http://localhost:5000/api/stream/1"
```

All endpoints should return valid JSON responses with appropriate HTTP status codes.
