# Audiobook Library - Quick Start

## 🚀 Launch the Library

### Using Systemd (Recommended)

```bash
# Start all services
systemctl --user start audiobooks.target

# Or start individually
systemctl --user start audiobooks-api audiobooks-proxy
```

### Manual Launch

```bash
cd /raid0/ClaudeCodeProjects/Audiobooks/library
./launch-v3.sh
```

Your browser will open to: **https://localhost:8443**

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
systemctl --user status audiobooks-api
systemctl --user start audiobooks-api
```

### Page loads but no books appear

**Problem:** Check browser console (F12) for errors

**Solution:**
1. Verify API is running: `curl -sk https://localhost:8443/api/stats`
2. Check browser console for JavaScript errors
3. Restart services: `systemctl --user restart audiobooks.target`

### Port already in use

**Problem:** Port 5001 or 8443 in use

**Solution:**
```bash
# Check what's using the ports
ss -tlnp | grep -E "5001|8443"

# Stop existing services
systemctl --user stop audiobooks-api audiobooks-proxy

# Restart
systemctl --user start audiobooks-api audiobooks-proxy
```

---

## 📊 API Endpoints

The library includes a REST API (proxied through HTTPS on port 8443):

```bash
# Get statistics
curl -sk https://localhost:8443/api/stats

# Search audiobooks
curl -sk "https://localhost:8443/api/audiobooks?search=tolkien"

# Filter by author
curl -sk "https://localhost:8443/api/audiobooks?author=sanderson"

# Get all filters (authors, narrators, etc.)
curl -sk https://localhost:8443/api/filters
```

---

## 🔄 Update Library After Adding Audiobooks

```bash
# Using systemctl (triggers database update)
systemctl --user start audiobooks-library-update.service

# Or manually
cd /raid0/ClaudeCodeProjects/Audiobooks/library/scanner
python3 scan_audiobooks.py

cd ../backend
python3 import_to_db.py

# Refresh browser (click "↻ Refresh" button)
```

---

## 📁 Service Architecture

| Service | Port | Description |
|---------|------|-------------|
| `audiobooks-api` | 5001 (localhost) | Flask REST API |
| `audiobooks-proxy` | 8443 (public) | HTTPS reverse proxy |
| `audiobooks-converter` | - | AAXC → OPUS conversion |
| `audiobooks-mover` | - | Move files from tmpfs |

---

## 📁 File Locations

- **Database:** `backend/audiobooks.db`
- **API Server:** `backend/api.py` (port 5001)
- **Proxy Server:** `web-v2/proxy_server.py` (port 8443)
- **Web Interface:** `web-v2/`

---

## 📚 Documentation

- `INSTALL.md` - Full installation guide
- `UPGRADE_GUIDE.md` - Features and deployment guide
- `PERFORMANCE_REPORT.md` - Benchmarks and analysis
- `../README.md` - Main project documentation

---

**Enjoy your audiobook library! 📚**

For issues or questions, check the documentation files listed above.
