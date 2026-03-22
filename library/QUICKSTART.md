# Audiobook Library - Quick Start

## 🚀 Launch the Library

### Using Systemd (Recommended)

```bash
# Start all services
sudo systemctl start audiobook.target

# Or start individually
sudo systemctl start audiobook-api audiobook-proxy audiobook-redirect
```

### Manual Launch (Development Only)

```bash
cd /opt/audiobooks/library
./launch-v2.sh  # Opens http://localhost:8090
```

Your browser will open to: **<https://localhost:8443>**

---

## ✅ Verify It's Working

You should see:

- **"The Library"** at the top
- **Statistics** showing your collection size
- **Book grid loading instantly** (not stuck on "Loading audiobooks...")
- **Pagination controls** at the bottom

---

## ⚠️ Browser Security Warning

You'll see a self-signed certificate warning. Click:

- **Chrome**: Advanced → Proceed to localhost
- **Firefox**: Advanced → Accept the Risk and Continue
- **Safari**: Show Details → visit this website

---

## 🔍 Features

- **Search** - Full-text search across all fields
- **Filter** - By author, narrator, collection
- **Sort** - By title, author, duration, date added
- **Pagination** - Browse 25/50/100/200 books per page
- **Collections** - Browse by category (Fiction, Mystery, Sci-Fi, etc.)
- **Back Office** - Database management, metadata editing, duplicate removal

---

## 🛠️ Troubleshooting

### "Error loading audiobooks"

**Problem:** API server not running

**Solution:**

```bash
sudo systemctl status audiobook-api
sudo systemctl start audiobook-api
```

### Page loads but no books appear

**Problem:** Check browser console (F12) for errors

**Solution:**

1. Verify API is running: `curl -sk https://localhost:8443/api/stats`
2. Check browser console for JavaScript errors
3. Restart services: `sudo systemctl restart audiobook.target`

### Port already in use

**Problem:** Port 5001 or 8443 in use

**Solution:**

```bash
# Check what's using the ports
ss -tlnp | grep -E "5001|8443"

# Stop existing services
sudo systemctl stop audiobook-api audiobook-proxy

# Restart
sudo systemctl start audiobook-api audiobook-proxy
```

### Services fail after reboot (tmpfs systems)

**Problem:** If `/tmp` or `/var` is mounted as tmpfs, required directories are cleared on reboot

**Solution:**

```bash
# Check if tmpfiles.d is configured
ls /etc/tmpfiles.d/audiobooks.conf

# If missing, deploy it:
sudo cp /opt/audiobooks/systemd/audiobooks-tmpfiles.conf /etc/tmpfiles.d/

# Create directories immediately:
sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf

# Verify directories exist:
ls -la /tmp/audiobook-staging /tmp/audiobook-triggers
```

See [INSTALL.md](INSTALL.md#tmpfs-considerations) for details.

---

## API Endpoints

The library includes a REST API (proxied through HTTPS on port 8443):

```bash
# Get statistics
curl -sk https://localhost:8443/api/stats

# Search audiobooks (response includes authors[] and narrators[] arrays)
curl -sk "https://localhost:8443/api/audiobooks?search=tolkien"

# Filter by author
curl -sk "https://localhost:8443/api/audiobooks?author=sanderson"

# Get audiobooks grouped by author or narrator (v7.0.0+)
curl -sk "https://localhost:8443/api/audiobooks/grouped?by=author"

# Get all filters (authors, narrators, etc.)
curl -sk https://localhost:8443/api/filters
```

---

## 🔄 Update Library After Adding Audiobooks

```bash
# Using systemctl (triggers database update)
sudo systemctl start audiobooks-library-update.service

# Or manually
cd /opt/audiobooks/library/scanner
python3 scan_audiobooks.py

cd ../backend
python3 import_to_db.py

# Refresh browser (click "↻ Refresh" button)
```

---

## 📁 Service Architecture

| Service | Port | Description |
|---------|------|-------------|
| `audiobook-api` | 5001 (localhost) | Flask REST API (Gunicorn+geventwebsocket) |
| `audiobook-proxy` | 8443 (public) | HTTPS reverse proxy |
| `audiobook-redirect` | 8080 (public) | HTTP to HTTPS redirect |
| `audiobook-converter` | - | AAXC → OPUS conversion |
| `audiobook-mover` | - | Move files from tmpfs |
| `audiobook-scheduler` | - | Maintenance task scheduler daemon |

---

## File Locations

- **Database:** `backend/audiobooks.db`
- **API Server:** `backend/api_server.py` (port 5001, using `api_modular/` package)
- **Proxy Server:** `web-v2/proxy_server.py` (port 8443)
- **Web Interface:** `web-v2/`
- **Name Parser:** `backend/name_parser.py` (author/narrator normalization)
- **Migrations:** `backend/migrations/` (schema 006-011+)

---

## Documentation

- `INSTALL.md` - Full installation guide
- `UPGRADE_GUIDE.md` - Features and deployment guide
- `PERFORMANCE_REPORT.md` - Benchmarks and analysis
- `../README.md` - Main project documentation
- `../docs/ARCHITECTURE.md` - System architecture

---

**Enjoy your audiobook library! 📚**

For issues or questions, check the documentation files listed above.
